"""iZone device discovery."""

import asyncio
from asyncio import (
    BaseTransport,
    CancelledError,
    Condition,
    DatagramProtocol,
    DatagramTransport,
    Lock,
    Task,
)
from collections.abc import Callable, Coroutine, Iterator
from contextlib import suppress
from ipaddress import IPv4Interface
import json
import logging
from types import TracebackType
from typing import Any, Self, cast

import aiohttp
from aiohttp import ClientSession, TCPConnector
import ifaddr

from .const import PLACEHOLDER_DEVICE_UID
from .controller import Controller
from .exceptions import raise_if_placeholder_uid
from .types import ControllerEndpoint
from .zone import Zone

# disposition: 1.4 | deprecate  (untagged = keep)
#   keep      — default; no tag required. Shared dual-track code.
#   1.4       — new consumer-driven discovery API
#   deprecate — legacy track; grep and delete when dual-track ends
#               (ship on 1.3.x until then). Sticky within a method until
#               the next disposition tag.

DISCOVERY_MSG = b"IASD"  # cspell:disable-line
DISCOVERY_PORT = 12107
UPDATE_PORT = 7005

# CHANGED_* — documented UDP protocol; recognize and ignore (no refresh). Dual-track.
CHANGED_SYSTEM = b"iZoneChanged_System"
CHANGED_ZONES = b"iZoneChanged_Zones"
CHANGED_SCHEDULES = b"iZoneChanged_Schedules"

# disposition: deprecate — internal _scan_loop timing (removed in 1.4e)
DISCOVERY_SLEEP = 5.0 * 60.0
DISCOVERY_RESCAN = 20.0
RESCAN_COOLDOWN = 5.0

# disposition: 1.4 — active discover_by_uid / discover_all wait
SCAN_TIMEOUT = 5.0

_LOG = logging.getLogger("pizone.discovery")

# disposition: 1.4 — create_discovery() singleton guard
_active_discovery: DiscoveryService | None = None


class LogExceptions:
    """Context manager that logs and suppresses listener exceptions."""

    def __init__(self, func: str) -> None:
        self.func = func

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type:
            _LOG.error(
                "Exception ignored when calling listener %s", self.func, exc_info=True
            )
        return True


# disposition: deprecate
class Listener:
    """Base class for iZone controller event listeners."""

    def controller_discovered(self, ctrl: Controller) -> None:
        """Handle a discovered controller.

        Also called for every existing controller when a new listener is
        registered.
        """

    def controller_disconnected(self, ctrl: Controller, ex: Exception) -> None:
        """Handle a lost controller connection.

        Args:
            ex: The exception that caused the disconnect.

        """

    def controller_reconnected(self, ctrl: Controller) -> None:
        """Handle a controller reconnect after a connection failure."""

    def controller_update(self, ctrl: Controller) -> None:
        """Handle refreshed controller system data."""

    def zone_update(self, ctrl: Controller, zone: Zone) -> None:
        """Handle refreshed zone data."""

    def power_update(self, ctrl: Controller) -> None:
        """Handle refreshed power monitor data."""


class DiscoveryService:
    """UDP discovery, endpoint registry, and controller factory.

    Obtain via :func:`create_discovery` (1.4) or :func:`discovery` (legacy).
    Direct construction is for tests and subclasses.
    """

    _controller_cls: type[Controller] = Controller

    def __init__(  # noqa: C901
        self,
        session: ClientSession | None = None,
        *,
        on_endpoint_discovered: Callable[[ControllerEndpoint], None] | None = None,
    ) -> None:
        """Create a discovery service.

        Prefer :func:`create_discovery` or :func:`discovery` instead of calling
        this directly.

        Call :meth:`start_discovery` or use the service as an async context
        manager to begin scanning.

        Args:
            session: Optional shared :class:`aiohttp.ClientSession`. When
                omitted, the service creates and owns its own session.
            on_endpoint_discovered: Optional callback for newly discovered
                controller endpoints.

        """
        self._close_task: Task[Any] | None = None
        # Both tracks: True = legacy discovery()/Listener path.
        self._legacy_pathway = False

        # disposition: deprecate — legacy listener/fetch registry
        self._controllers: dict[str, Controller] = {}
        self._pending_init: set[str] = set()
        self._disconnected: set[str] = set()
        self._listeners: list[Listener] = []

        # disposition: 1.4 — endpoint ownership (UID is identity; host may move)
        # _known_endpoints: heard about, unclaimed (UDP and/or discover_by_*).
        # _claimed_endpoints: create_controller claimed; removed on Controller.close().
        # No stale/disappear tracking — notify is unverified UDP; create re-probes.
        self._on_endpoint_discovered = on_endpoint_discovered
        self._known_endpoints: dict[str, ControllerEndpoint] = {}
        self._claimed_endpoints: dict[str, ControllerEndpoint] = {}
        self._scan_collector: dict[str, str] | None = None
        self._discover_lock = Lock()
        # Set during discover_all wait so passive notifies dedupe with verify fan-out.
        self._discover_all_notified: set[str] | None = None

        _LOG.info("Starting discovery protocol")
        self._session = session
        self._own_session = session is None

        self._transport: DatagramTransport | None = None

        # disposition: deprecate
        self._scan_condition = Condition()
        self._last_rescan_time: float = 0.0

        self._tasks: list[Task[Any]] = []

        _srv = self

        # disposition: deprecate
        class _EventCoordinator(Listener):
            """Fan-out adapter that dispatches controller and zone events to listeners."""

            def controller_discovered(self, ctrl: Controller) -> None:
                _LOG.info(
                    "New controller found: id=%s ip=%s", ctrl.device_uid, ctrl.device_ip
                )
                for listener in _srv._listeners:
                    with LogExceptions("controller_discovered"):
                        listener.controller_discovered(ctrl)

            def controller_disconnected(self, ctrl: Controller, ex: Exception) -> None:
                _LOG.warning(
                    "Connection to controller lost: id=%s ip=%s",
                    ctrl.device_uid,
                    ctrl.device_ip,
                )
                _srv._disconnected.add(ctrl.device_uid)
                _srv.create_task(_srv._rescan())
                for listener in _srv._listeners:
                    with LogExceptions("controller_disconnected"):
                        listener.controller_disconnected(ctrl, ex)

            def controller_reconnected(self, ctrl: Controller) -> None:
                _LOG.warning(
                    "Controller reconnected: id=%s ip=%s",
                    ctrl.device_uid,
                    ctrl.device_ip,
                )
                _srv._disconnected.discard(ctrl.device_uid)
                for listener in _srv._listeners:
                    with LogExceptions("controller_reconnected"):
                        listener.controller_reconnected(ctrl)

            def controller_update(self, ctrl: Controller) -> None:
                for listener in _srv._listeners:
                    with LogExceptions("controller_update"):
                        listener.controller_update(ctrl)

            def zone_update(self, ctrl: Controller, zone: Zone) -> None:
                for listener in _srv._listeners:
                    with LogExceptions("zone_update"):
                        listener.zone_update(ctrl, zone)

            def power_update(self, ctrl: Controller) -> None:
                for listener in _srv._listeners:
                    with LogExceptions("power_update"):
                        listener.power_update(ctrl)

        self._event_coordinator: Listener = _EventCoordinator()

    async def __aenter__(self) -> Self:
        await self.start_discovery()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    def _task_done_callback(self, task: Task[Any]) -> None:
        try:
            if task.exception():
                _LOG.exception("Uncaught exception", exc_info=task.exception())
        except CancelledError:
            pass
        self._tasks.remove(task)

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> Task[Any]:
        """Create a tracked task in the current event loop."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.append(task)

        task.add_done_callback(self._task_done_callback)
        return task

    # disposition: deprecate
    def add_listener(self, listener: Listener) -> None:
        """Register a listener for controller and zone events.

        All existing controllers are passed to the listener immediately via
        :meth:`~pizone.discovery.Listener.controller_discovered`.
        """
        self._listeners.append(listener)

        def callback() -> None:
            for controller in self._controllers.values():
                listener.controller_discovered(controller)

        asyncio.get_running_loop().call_soon(callback)

    # disposition: deprecate
    def remove_listener(self, listener: Listener) -> None:
        """Remove a previously registered listener.

        Raises:
            ValueError: If *listener* is not registered.

        """
        self._listeners.remove(listener)

    async def start_discovery(self) -> None:
        """Start the UDP discovery protocol and scan loop.

        Raises:
            OSError: If the discovery socket cannot be created or bound.
            RuntimeError: If :meth:`start_discovery` is called when a
                transport is already active.

        """
        if self._own_session:
            # Mitigation for hubs that stall under HTTP reuse (core#176536).
            self._session = ClientSession(connector=TCPConnector(force_close=True))

        _svc = self

        class _UDPTransport(DatagramProtocol):
            def connection_made(self, transport: BaseTransport) -> None:
                _svc._on_connection_made(cast(DatagramTransport, transport))

            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                _svc._on_datagram_received(data, addr)

            def connection_lost(self, exc: Exception | None) -> None:
                _svc._on_connection_lost(exc)

            def error_received(self, exc: Exception) -> None:
                _svc._on_error_received(exc)

        await asyncio.get_running_loop().create_datagram_endpoint(
            _UDPTransport,
            local_addr=("0.0.0.0", UPDATE_PORT),
            allow_broadcast=True,
        )

    def _on_connection_made(self, transport: DatagramTransport) -> None:
        if self._close_task:
            transport.close()
            return
        assert not self._transport, "Another connection made"

        self._transport = transport
        # disposition: deprecate — scan loop only on legacy pathway (removed in 1.4e)
        if self._legacy_pathway:
            self.create_task(self._scan_loop())

    def _get_broadcasts(self) -> Iterator[str]:
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if not isinstance(ip.ip, str):
                    continue
                interface = IPv4Interface(f"{ip.ip}/{ip.network_prefix}")
                yield str(interface.network.broadcast_address)

    def _send_broadcasts(self) -> None:
        assert self._transport is not None, "Discovery transport is not ready"
        for broadcast in self._get_broadcasts():
            _LOG.debug("Sending discovery message to addr %s", broadcast)
            self._transport.sendto(DISCOVERY_MSG, (broadcast, DISCOVERY_PORT))

    # disposition: deprecate
    async def _scan_loop(self) -> None:
        assert self._transport, "Should be impossible"

        while True:
            self._send_broadcasts()

            try:
                async with asyncio.timeout(
                    DISCOVERY_RESCAN if self._disconnected else DISCOVERY_SLEEP
                ):
                    async with self._scan_condition:
                        await self._scan_condition.wait()
            except TimeoutError:
                pass

            if self._close_task:
                return

    # disposition: deprecate
    async def _rescan(self) -> None:
        async with self._scan_condition:
            self._scan_condition.notify()

    # disposition: deprecate
    async def _maybe_rescan(self) -> None:
        """Trigger a rescan only if outside the cool-down window."""
        now = asyncio.get_running_loop().time()
        if now - self._last_rescan_time >= RESCAN_COOLDOWN:
            self._last_rescan_time = now
            await self._rescan()

    # disposition: 1.4
    async def scan(self) -> None:
        """Send an IASD broadcast on all local interfaces."""
        if self._transport is None:
            raise ConnectionError("Discovery transport is not ready")
        self._send_broadcasts()

    # disposition: 1.4
    def schedule_cooled_scan(self) -> None:
        """Fire a non-blocking ``scan()`` if outside the rescan cooldown."""
        if self.is_closed:
            return
        now = asyncio.get_running_loop().time()
        if now - self._last_rescan_time < RESCAN_COOLDOWN:
            return
        self._last_rescan_time = now

        async def _run() -> None:
            if self.is_closed:
                return
            try:
                await self.scan()
            except Exception:  # noqa: BLE001
                _LOG.debug("Cooled scan nudge failed", exc_info=True)

        asyncio.get_running_loop().create_task(_run())

    # disposition: 1.4
    def _ensure_endpoint_available(self, endpoint: ControllerEndpoint) -> None:
        """Raise if *endpoint* is already claimed."""
        if endpoint.uid in self._claimed_endpoints:
            raise RuntimeError(f"Controller {endpoint.uid} already created")

    # disposition: 1.4
    def _claim_endpoint(self, endpoint: ControllerEndpoint) -> None:
        """Move an endpoint from discovered to claimed."""
        self._ensure_endpoint_available(endpoint)
        self._known_endpoints.pop(endpoint.uid, None)
        self._claimed_endpoints[endpoint.uid] = endpoint

    # disposition: 1.4
    def _cache_endpoint(self, endpoint: ControllerEndpoint) -> None:
        """Remember an unclaimed endpoint for later discovery calls."""
        self._ensure_endpoint_available(endpoint)
        self._known_endpoints[endpoint.uid] = endpoint

    # disposition: 1.4
    def _emit_endpoint_discovered(self, endpoint: ControllerEndpoint) -> None:
        """Invoke on_endpoint_discovered and record uid for discover_all dedupe."""
        if self._on_endpoint_discovered:
            with LogExceptions("on_endpoint_discovered"):
                self._on_endpoint_discovered(endpoint)
        if self._discover_all_notified is not None:
            self._discover_all_notified.add(endpoint.uid)

    # disposition: 1.4
    def _endpoint_by_host(
        self, endpoints: dict[str, ControllerEndpoint], host: str
    ) -> ControllerEndpoint | None:
        for endpoint in endpoints.values():
            if endpoint.host == host:
                return endpoint
        return None

    # disposition: 1.4
    async def discover_by_host(self, host: str) -> ControllerEndpoint | None:
        """Return a known endpoint for *host*, or HTTP-probe if unseen.

        User-initiated lookup; HTTP verify on probe. Cache hits may be older UDP data.

        Raises:
            UnpairedBridgeError: If the probed UID is the unpaired placeholder;
                the endpoint is not cached.

        """
        claimed = self._endpoint_by_host(self._claimed_endpoints, host)
        if claimed is not None:
            raise RuntimeError(f"Controller {claimed.uid} already created")
        known = self._endpoint_by_host(self._known_endpoints, host)
        if known is not None:
            return known

        probed = await self._probe(host)
        if probed is None:
            return None
        endpoint, _settings = probed
        raise_if_placeholder_uid(endpoint.uid)
        self._cache_endpoint(endpoint)
        return endpoint

    # disposition: 1.4
    async def discover_by_uid(self, uid: str) -> ControllerEndpoint | None:
        """Broadcast IASD, wait for *uid*, and HTTP-verify the response.

        User-initiated lookup. Cache hits skip re-probe.

        Raises:
            UnpairedBridgeError: If *uid* is the unpaired placeholder (no I/O).

        """
        raise_if_placeholder_uid(uid)
        if uid in self._claimed_endpoints:
            raise RuntimeError(f"Controller {uid} already created")
        endpoint = self._known_endpoints.get(uid)
        if endpoint is not None:
            return endpoint

        async with self._discover_lock:
            endpoint = self._known_endpoints.get(uid)
            if endpoint is not None:
                return endpoint

            collector: dict[str, str] = {}
            self._scan_collector = collector
            try:
                await self.scan()
                await asyncio.sleep(SCAN_TIMEOUT)
            finally:
                self._scan_collector = None

            host = collector.get(uid)
            if host is None:
                return None

            probed = await self._probe(host)
            if probed is None:
                return None
            endpoint, _settings = probed
            if endpoint.uid != uid:
                return None
            self._cache_endpoint(endpoint)
            return endpoint

    # disposition: 1.4
    async def discover_all(self) -> list[ControllerEndpoint]:
        """Broadcast IASD, collect replies, and HTTP-verify each endpoint.

        Invokes ``on_endpoint_discovered`` once per verified uid for this call.
        ASPort during the wait uses the normal discovery notify path (new /
        host-change only); after verify, any uid not already notified in this
        call is notified once so a user-initiated scan still fan-outs when
        nothing new arrived over UDP (e.g. already-known same-host).
        """
        async with self._discover_lock:
            notified: set[str] = set()
            self._discover_all_notified = notified
            collector: dict[str, str] = {
                uid: endpoint.host for uid, endpoint in self._known_endpoints.items()
            }
            self._scan_collector = collector
            try:
                await self.scan()
                await asyncio.sleep(SCAN_TIMEOUT)
            finally:
                self._scan_collector = None
                self._discover_all_notified = None

            verified: list[ControllerEndpoint] = []
            seen_hosts: set[str] = set()
            for host in collector.values():
                if host in seen_hosts:
                    continue
                seen_hosts.add(host)
                probed = await self._probe(host)
                if probed is None:
                    continue
                endpoint, _settings = probed
                if endpoint.uid == PLACEHOLDER_DEVICE_UID:
                    continue
                try:
                    self._cache_endpoint(endpoint)
                except RuntimeError:
                    continue
                verified.append(endpoint)
                if endpoint.uid not in notified:
                    self._emit_endpoint_discovered(endpoint)
                    notified.add(endpoint.uid)
            return verified

    # disposition: 1.4
    async def create_controller(
        self,
        uid: str,
        host: str,
        *,
        on_address_changed: Callable[[ControllerEndpoint], None] | None = None,
    ) -> Controller:
        """Create and initialize a controller at the given address.

        Preferred 1.4 entry point after :func:`create_discovery`. One-shot per
        UID. HTTP probe is the trust boundary; callers must handle the device
        being unreachable even after a recent discovery notify.

        Raises:
            UnpairedBridgeError: If *uid* is the unpaired placeholder (no I/O).
            RuntimeError: If a controller with *uid* already exists.
            ConnectionError: If the controller cannot be reached after
                address fallback.
            ControllerCommandError: If the device rejects the protocol request.
            ResponseDecodeError: If the device returns malformed data.
            KeyError: If a required field is missing from a device response.

        """
        raise_if_placeholder_uid(uid)
        if uid in self._claimed_endpoints:
            raise RuntimeError(f"Controller {uid} already created")
        pending_address_changed: ControllerEndpoint | None = None

        try:
            controller = await self._connect_controller_at(
                uid, host, on_address_changed=on_address_changed
            )
        except ConnectionError:
            endpoint = await self.discover_by_uid(uid)
            if endpoint is None or endpoint.host == host:
                raise
            pending_address_changed = ControllerEndpoint(uid=uid, host=endpoint.host)
            controller = await self._connect_controller_at(
                uid,
                endpoint.host,
                on_address_changed=on_address_changed,
            )

        if pending_address_changed is not None and on_address_changed is not None:
            self.schedule_address_changed(on_address_changed, pending_address_changed)
        return controller

    # disposition: 1.4
    def schedule_address_changed(
        self,
        callback: Callable[[ControllerEndpoint], None],
        endpoint: ControllerEndpoint,
    ) -> None:
        """Schedule a one-shot address-changed callback after the caller returns."""
        claimed = self._claimed_endpoints.get(endpoint.uid)
        if claimed is not None:
            self._claimed_endpoints[endpoint.uid] = endpoint
        asyncio.get_running_loop().create_task(
            _invoke_address_changed(callback, endpoint)
        )

    # disposition: 1.4
    async def _connect_controller_at(
        self,
        uid: str,
        host: str,
        *,
        on_address_changed: Callable[[ControllerEndpoint], None] | None,
    ) -> Controller:
        probed = await self._probe(host)
        if probed is None:
            raise ConnectionError(f"Unable to connect to controller {uid} at {host}")
        endpoint, system_settings = probed
        if endpoint.uid != uid:
            raise ConnectionError(f"Unable to connect to controller {uid} at {host}")

        controller = await self._controller_cls.create(
            self,
            self._event_coordinator,
            endpoint=endpoint,
            system_settings=cast(Controller.ControllerData, system_settings),
            on_address_changed=on_address_changed,
        )
        self._controllers[uid] = controller
        self._claim_endpoint(endpoint)
        return controller

    # disposition: 1.4
    def _release_endpoint(self, endpoint: ControllerEndpoint) -> None:
        """Move a claimed endpoint back to the discovered pool."""
        if endpoint.uid not in self._claimed_endpoints:
            return
        self._claimed_endpoints.pop(endpoint.uid)
        self._known_endpoints[endpoint.uid] = endpoint

    # disposition: 1.4
    def _controller_closed(self, controller: Controller) -> None:
        """Release a closed controller back to the unclaimed endpoint cache."""
        self._controllers.pop(controller.device_uid, None)
        self._release_endpoint(
            ControllerEndpoint(uid=controller.device_uid, host=controller.device_ip)
        )

    # disposition: 1.4
    async def _probe(
        self, host: str
    ) -> tuple[ControllerEndpoint, dict[str, Any]] | None:
        session = self._session
        if session is None:
            return None
        try:
            async with session.get(
                f"http://{host}/SystemSettings",
                headers={"Connection": "close"},
                timeout=aiohttp.ClientTimeout(total=Controller.REQUEST_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    return None
                try:
                    data = await response.json(content_type=None)
                except json.JSONDecodeError:
                    text = await response.text()
                    if len(text) >= 4 and text[-4:] == "{OK}":
                        data = json.loads(text[:-4])
                    else:
                        return None
        except TimeoutError, aiohttp.ClientError, OSError:
            return None

        uid = data.get("AirStreamDeviceUId")
        if not isinstance(uid, str) or not uid:
            return None
        return ControllerEndpoint(uid=uid, host=host), data

    # disposition: deprecate
    async def fetch_controller(
        self, uid: str, timeout: float | None = None
    ) -> Controller | None:
        """Return the controller with *uid*.

        When *timeout* is provided, waits up to that many seconds for the
        controller to appear via UDP discovery.

        Does not raise if the controller is not found; returns ``None`` instead.
        """
        if uid in self._controllers:
            return self._controllers[uid]
        if timeout is None:
            return None

        ready = asyncio.Event()

        class _WaitForUidListener(Listener):
            def controller_discovered(self, ctrl: Controller) -> None:
                if ctrl.device_uid == uid:
                    ready.set()

        listener = _WaitForUidListener()
        self.add_listener(listener)
        try:
            # Re-check after registering the listener (add_listener uses call_soon,
            # so no race possible in the single-threaded event loop).
            if uid in self._controllers:
                return self._controllers[uid]

            await self._maybe_rescan()

            with suppress(TimeoutError):
                async with asyncio.timeout(timeout):
                    await ready.wait()
        finally:
            self.remove_listener(listener)

        return self._controllers.get(uid)

    # disposition: deprecate
    async def fetch_controllers(
        self, timeout: float | None = None
    ) -> dict[str, Controller]:
        """Return all controllers currently known to the discovery service.

        When *timeout* is provided, triggers a rescan and waits before
        returning the registry snapshot.

        Does not raise if no controllers are found.
        """
        if timeout is None:
            return dict(self._controllers)

        await self._maybe_rescan()
        await asyncio.sleep(timeout)
        return dict(self._controllers)

    async def close(self) -> None:
        """Stop discovery, close the UDP transport, and cancel tracked tasks."""
        current = asyncio.current_task()
        if self._close_task and self._close_task is not current:
            with suppress(CancelledError):
                await self._close_task
            return
        _LOG.info("Close called on discovery service.")
        self._close_task = current
        if self._transport:
            self._transport.close()

        pending = [task for task in list(self._tasks) if task is not current]
        for task in pending:
            task.cancel()

        if self._own_session and self._session:
            await self._session.close()

        if pending:
            await asyncio.wait(pending)

        _clear_discovery_global(self)

    def _on_connection_lost(self, exc: Exception | None) -> None:
        _LOG.debug("Connection Lost")
        if not self._close_task:
            _LOG.error("Connection Lost unexpectedly: %s", repr(exc))
            self.create_task(self.close())

    @property
    def is_closed(self) -> bool:
        """Return whether the discovery service is closed."""
        if self._transport:
            return self._transport.is_closing()
        return self._close_task is not None

    @property
    def session(self) -> ClientSession | None:
        """Return the aiohttp session used for HTTP requests."""
        return self._session

    def _on_error_received(self, _: Exception) -> None:
        _LOG.warning("Error passed and ignored to error_received", exc_info=True)

    def _find_by_addr(self, addr: tuple[str, int]) -> Controller | None:
        for ctrl in self._controllers.values():
            if ctrl.device_ip == addr[0]:
                return ctrl
        return None

    def _on_datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        _LOG.debug("Datagram Received %s", data)
        if self._close_task:
            return
        self._process_datagram(data, addr)

    def _process_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if data == DISCOVERY_MSG:
            return
        if data in (CHANGED_SYSTEM, CHANGED_ZONES, CHANGED_SCHEDULES):
            _LOG.debug("Ignoring push notification %s from %s", data, addr)
            return
        if self._legacy_pathway:
            self._discovery_received_deprecated(data)
        else:
            self._discovery_received(data)

    # disposition: deprecate
    def _parse_datagram_deprecated(
        self, data: bytes
    ) -> tuple[list[str], str, str] | None:
        try:
            message = data.decode().split(",")
        except UnicodeDecodeError:
            return None
        if len(message) < 3 or message[0] != "ASPort_12107":
            return None
        device_uid = message[1].split("_")[1]
        device_ip = message[2].split("_")[1]
        return message, device_uid, device_ip

    # disposition: deprecate
    def _discovery_received_deprecated(self, data: bytes) -> None:
        parsed = self._parse_datagram_deprecated(data)
        if parsed is None:
            _LOG.warning("Invalid Message Received: %s", data.decode(errors="replace"))
            return

        message, device_uid, device_ip = parsed

        if device_uid == PLACEHOLDER_DEVICE_UID:
            return

        if device_uid not in self._controllers:
            if device_uid in self._pending_init:
                return

            is_v2 = len(message) >= 4 and "iZoneV2" in message[3:]
            is_ipower = len(message) >= 4 and "iPower" in message[3:]
            controller = self._create_controller(
                device_uid, device_ip, is_v2, is_ipower
            )

            self._pending_init.add(device_uid)

            async def initialize_controller() -> None:
                try:
                    try:
                        await controller._initialize()  # noqa: SLF001
                    except ConnectionError as ex:
                        _LOG.warning(
                            "Can't connect to discovered server at IP '%s' exception: %s",
                            device_ip,
                            repr(ex),
                        )
                        return

                    self._controllers[device_uid] = controller
                    self._event_coordinator.controller_discovered(controller)
                finally:
                    self._pending_init.discard(device_uid)

            self.create_task(initialize_controller())
        else:
            controller = self._controllers[device_uid]
            controller._refresh_address(device_ip)  # noqa: SLF001

    # disposition: 1.4
    def _parse_datagram(self, data: bytes) -> ControllerEndpoint | None:
        try:
            message = data.decode().split(",")
        except UnicodeDecodeError:
            return None
        if len(message) < 3 or message[0] != "ASPort_12107":
            return None
        device_uid = message[1].split("_")[1]
        device_host = message[2].split("_")[1]
        return ControllerEndpoint(uid=device_uid, host=device_host)

    # disposition: 1.4
    def _discovery_received(self, data: bytes) -> None:
        """Handle passive ASPort: cache/notify unclaimed; address-change claimed."""
        endpoint = self._parse_datagram(data)
        if endpoint is None:
            _LOG.warning("Invalid Message Received: %s", data.decode(errors="replace"))
            return

        if endpoint.uid == PLACEHOLDER_DEVICE_UID:
            return

        if self._scan_collector is not None:
            self._scan_collector[endpoint.uid] = endpoint.host

        claimed = self._claimed_endpoints.get(endpoint.uid)
        if claimed is not None:
            if claimed.host != endpoint.host:
                self._claimed_endpoints[endpoint.uid] = endpoint
                controller = self._controllers.get(endpoint.uid)
                if controller is not None:
                    controller._refresh_address(endpoint.host)  # noqa: SLF001
            return

        previous = self._known_endpoints.get(endpoint.uid)
        newly_seen = previous is None
        host_changed = previous is not None and previous.host != endpoint.host
        with suppress(RuntimeError):
            self._cache_endpoint(endpoint)
        if newly_seen or host_changed:
            self._emit_endpoint_discovered(endpoint)

    def _create_controller(
        self, device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> Controller:
        return self._controller_cls.from_discovery(
            self,
            self._event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )


# disposition: deprecate
def discovery(
    *listeners: Listener, session: ClientSession | None = None
) -> DiscoveryService:
    """Create a legacy discovery service.

    Preferred entry point for the legacy track. Prefer
    :func:`create_discovery` for new code. Controllers appear via listeners
    and :meth:`DiscoveryService.fetch_controller`; do not construct them
    directly.

    The returned object is an async context manager and can also be started
    manually with :meth:`DiscoveryService.start_discovery`.

    Args:
        listeners: Optional listeners registered before discovery starts.
        session: Optional shared :class:`aiohttp.ClientSession`.

    """
    service = DiscoveryService(session=session)
    service._legacy_pathway = True  # noqa: SLF001
    for listener in listeners:
        service.add_listener(listener)
    return service


# disposition: 1.4
def _clear_discovery_global(service: DiscoveryService) -> None:
    global _active_discovery  # noqa: PLW0603
    if _active_discovery is service:
        _active_discovery = None


# disposition: 1.4
async def _invoke_address_changed(
    callback: Callable[[ControllerEndpoint], None],
    endpoint: ControllerEndpoint,
) -> None:
    with LogExceptions("on_address_changed"):
        callback(endpoint)


# disposition: 1.4
async def create_discovery(
    *,
    on_endpoint_discovered: Callable[[ControllerEndpoint], None] | None = None,
    session: ClientSession | None = None,
) -> DiscoveryService:
    """Create the process-global discovery service.

    Preferred 1.4 entry point. Use :meth:`DiscoveryService.create_controller`
    (and the ``discover_*`` helpers) on the returned service; do not construct
    :class:`~pizone.controller.Controller` instances directly.

    Args:
        on_endpoint_discovered: Called for each newly discovered endpoint.
        session: Optional shared :class:`aiohttp.ClientSession`. When omitted,
            the service creates and owns its own session.

    Raises:
        RuntimeError: If a discovery service already exists.

    """
    global _active_discovery  # noqa: PLW0603
    if _active_discovery is not None:
        raise RuntimeError("Discovery service already created")
    service = DiscoveryService(
        session=session,
        on_endpoint_discovered=on_endpoint_discovered,
    )
    _active_discovery = service
    await service.start_discovery()
    return service
