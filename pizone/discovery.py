"""iZone device discovery."""

import asyncio
from asyncio import (
    BaseTransport,
    CancelledError,
    DatagramProtocol,
    DatagramTransport,
    Event,
    Lock,
    Task,
)
from collections import deque
from collections.abc import Callable, Coroutine, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from ipaddress import IPv4Address, IPv4Interface
import json
import logging
import socket
import time
from types import TracebackType
from typing import Any, Self, cast

import aiohttp
from aiohttp import ClientSession
import ifaddr

from . import v2 as v2_mod
from .const import PLACEHOLDER_DEVICE_UID
from .controller import Controller
from .exceptions import ControllerAlreadyClaimedError, raise_if_placeholder_uid
from .types import ControllerEndpoint, ControllerProbe

DISCOVERY_MSG = b"IASD"  # cspell:disable-line
DISCOVERY_PORT = 12107
UPDATE_PORT = 7005

# CHANGED_* — documented UDP push notifications; recognized and ignored (no refresh).
CHANGED_SYSTEM = b"iZoneChanged_System"
CHANGED_ZONES = b"iZoneChanged_Zones"
CHANGED_SCHEDULES = b"iZoneChanged_Schedules"

# Minimum gap between cooled scans nudged by HTTP transport failures.
RESCAN_COOLDOWN = 5.0

# Active discover_by_uid / discover_all wait for ASPort replies.
SCAN_TIMEOUT = 5.0
TRANSPORT_CLOSE_TIMEOUT = 1.0

# Inbound UDP datagrams retained for diagnostics (ASPort, CHANGED_*, garbage).
UDP_REPLY_BUFFER_SIZE = 20

_LOG = logging.getLogger("pizone.discovery")

# create_discovery() singleton guard.
_active_discovery: DiscoveryService | None = None


class LogExceptions:
    """Context manager that logs and suppresses consumer callback exceptions."""

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
                "Exception ignored when calling callback %s", self.func, exc_info=True
            )
        return True


class _State(Enum):
    """Lifecycle of one controller UID in the discovery registry."""

    SEEN = auto()  # Passive UDP only; HTTP verify in flight.
    KNOWN = auto()  # HTTP-verified and unclaimed; re-probed only on a host move.
    CLAIMING = auto()  # create_controller() in flight.
    CLAIMED = auto()  # A live controller owns it.


@dataclass(slots=True)
class _Entry:
    state: _State
    endpoint: ControllerEndpoint
    controller: Controller | None = None  # CLAIMED only
    # In-flight HTTP verify of a UDP-reported host. For SEEN it is the endpoint;
    # for KNOWN/CLAIMED a move that applies only once it answers.
    candidate: ControllerEndpoint | None = None
    verify: Task[ControllerEndpoint | None] | None = None


class DiscoveryService:
    """UDP discovery, endpoint registry, and controller factory.

    Obtain via :func:`create_discovery`. Direct construction is for tests and
    subclasses.
    """

    _controller_cls: type[Controller] = Controller

    def __init__(
        self,
        session: ClientSession | None = None,
        *,
        on_endpoint_discovered: Callable[[ControllerEndpoint], None] | None = None,
    ) -> None:
        """Create a discovery service.

        Prefer :func:`create_discovery` instead of calling this directly.

        Call :meth:`start_discovery` or use the service as an async context
        manager to begin scanning.

        Args:
            session: Optional shared :class:`aiohttp.ClientSession`. When
                omitted, the service creates and owns its own session.
            on_endpoint_discovered: Optional callback for newly discovered
                controller endpoints, fired only after an HTTP verify.

        """
        self._close_task: Task[Any] | None = None

        # One entry per UID (identity; host may move). Notify fires only after
        # an HTTP verify; create still re-probes at setup.
        self._on_endpoint_discovered = on_endpoint_discovered
        self._endpoints: dict[str, _Entry] = {}
        # Ring buffer of recent inbound UDP datagrams (diagnostics).
        self._recent_udp: deque[dict[str, Any]] = deque(maxlen=UDP_REPLY_BUFFER_SIZE)
        self._scan_collector: dict[str, str] | None = None
        self._discover_lock = Lock()
        # Set during discover_all wait so passive notifies dedupe with verify fan-out.
        self._discover_all_notified: set[str] | None = None

        _LOG.info("Starting discovery protocol")
        self._session = session
        self._own_session = session is None

        self._transport: DatagramTransport | None = None
        # Set by connection_lost once the event loop has released the socket.
        self._transport_released = Event()

        self._last_rescan_time: float = 0.0

        self._tasks: list[Task[Any]] = []

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

    async def start_discovery(self) -> None:
        """Start the UDP discovery protocol.

        Raises:
            OSError: If the discovery socket cannot be created or bound.
            RuntimeError: If :meth:`start_discovery` is called when a
                transport is already active.

        """
        if self._own_session:
            self._session = ClientSession()

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

    def _get_broadcasts(self) -> Iterator[str]:
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if not isinstance(ip.ip, str):
                    continue
                interface = IPv4Interface(f"{ip.ip}/{ip.network_prefix}")
                yield str(interface.network.broadcast_address)

    def _send_broadcasts(self) -> None:
        transport = self._transport
        if transport is None:
            # Closing or closed: nothing to send on.
            return
        for broadcast in self._get_broadcasts():
            _LOG.debug("Sending discovery message to addr %s", broadcast)
            transport.sendto(DISCOVERY_MSG, (broadcast, DISCOVERY_PORT))

    async def scan(self) -> None:
        """Send an IASD broadcast on all local interfaces."""
        if self._transport is None:
            raise ConnectionError("Discovery transport is not ready")
        self._send_broadcasts()

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

    def _entry(self, uid: str, *states: _State) -> _Entry | None:
        """Return the entry for *uid* when it is in one of *states*."""
        entry = self._endpoints.get(uid)
        if entry is None or entry.state not in states:
            return None
        return entry

    def _entry_by_host(self, host: str, *states: _State) -> _Entry | None:
        """Return the first entry at *host* in one of *states*."""
        for entry in self._endpoints.values():
            if entry.endpoint.host == host and entry.state in states:
                return entry
        return None

    def _verifying_host(self, host: str) -> ControllerEndpoint | None:
        """Return an endpoint at *host* whose HTTP verify is in flight."""
        for entry in self._endpoints.values():
            if (
                entry.state is not _State.CLAIMING
                and entry.candidate is not None
                and entry.candidate.host == host
            ):
                return entry.candidate
        return None

    def _endpoints_in(self, state: _State) -> dict[str, ControllerEndpoint]:
        """Return a snapshot of endpoints in *state*, keyed by UID."""
        return {
            uid: entry.endpoint
            for uid, entry in self._endpoints.items()
            if entry.state is state
        }

    def _controller(self, uid: str) -> Controller | None:
        """Return the live controller that claimed *uid*, if any."""
        entry = self._entry(uid, _State.CLAIMED)
        return entry.controller if entry is not None else None

    def _ensure_uid_available(self, uid: str) -> None:
        """Raise if *uid* is claimed or being claimed."""
        if self._entry(uid, _State.CLAIMING, _State.CLAIMED) is not None:
            raise ControllerAlreadyClaimedError(f"Controller {uid} already created")

    def _claim(self, controller: Controller) -> None:
        """Record *controller* as the owner of its UID."""
        self._endpoints[controller.device_uid] = _Entry(
            _State.CLAIMED,
            ControllerEndpoint(uid=controller.device_uid, host=controller.device_ip),
            controller,
        )

    def _apply_verified(self, endpoint: ControllerEndpoint) -> None:
        """Record that *endpoint*'s UID answered over HTTP at its IP.

        Repoints a running controller (firing its address-changed callback),
        updates or adds an unclaimed endpoint and notifies, and leaves an
        in-flight create alone.
        """
        entry = self._endpoints.get(endpoint.uid)
        if entry is not None and entry.state is _State.CLAIMING:
            return
        if entry is not None and entry.state is _State.CLAIMED:
            if entry.endpoint != endpoint:
                entry.endpoint = endpoint
                if entry.controller is not None:
                    entry.controller._refresh_address(endpoint.host)  # noqa: SLF001
            return
        if (
            entry is not None
            and entry.state is _State.KNOWN
            and entry.endpoint == endpoint
        ):
            return
        self._endpoints[endpoint.uid] = _Entry(_State.KNOWN, endpoint)
        self._emit_endpoint_discovered(endpoint)

    def _verify_endpoint(
        self, endpoint: ControllerEndpoint
    ) -> Task[ControllerEndpoint | None]:
        """Return the in-flight HTTP verify for *endpoint*, starting one if needed.

        A KNOWN or CLAIMED entry keeps its current endpoint until the new host
        answers. The caller must have checked that the UID is not claiming.
        """
        entry = self._endpoints.get(endpoint.uid)
        if (
            entry is not None
            and entry.candidate == endpoint
            and entry.verify is not None
            and not entry.verify.done()
        ):
            return entry.verify
        if entry is None or entry.state is _State.SEEN:
            entry = _Entry(_State.SEEN, endpoint)
            self._endpoints[endpoint.uid] = entry
        entry.candidate = endpoint
        entry.verify = self.create_task(self._run_verify(entry, endpoint))
        return entry.verify

    async def _await_verify(
        self, endpoint: ControllerEndpoint
    ) -> ControllerEndpoint | None:
        """Wait for *endpoint* to verify; a cancelled waiter leaves the verify running."""
        return await asyncio.shield(self._verify_endpoint(endpoint))

    async def _run_verify(
        self, entry: _Entry, candidate: ControllerEndpoint
    ) -> ControllerEndpoint | None:
        """HTTP-verify *candidate* for *entry*; apply it, or drop it."""
        uid = candidate.uid
        probed = await self._probe(candidate.host, expected_uid=uid)
        if self._endpoints.get(uid) is not entry or entry.candidate != candidate:
            # Superseded by a newer host, a claim, a release, or a direct probe.
            return None
        entry.candidate = None
        if probed is None or uid == PLACEHOLDER_DEVICE_UID:
            if entry.state is _State.SEEN:
                del self._endpoints[uid]
            return None
        self._apply_verified(probed.endpoint)
        return probed.endpoint

    def _emit_endpoint_discovered(self, endpoint: ControllerEndpoint) -> None:
        """Invoke on_endpoint_discovered and record uid for discover_all dedupe."""
        if self._on_endpoint_discovered:
            with LogExceptions("on_endpoint_discovered"):
                self._on_endpoint_discovered(endpoint)
        if self._discover_all_notified is not None:
            self._discover_all_notified.add(endpoint.uid)

    async def discover_by_host(self, host: str) -> ControllerEndpoint | None:
        """Return the controller endpoint at *host*, an IPv4 address or a name.

        A name is resolved to IPv4 addresses first; the returned endpoint always
        holds the IP. An address already in the registry is returned without a
        probe. Otherwise the first address that answers over HTTP is recorded
        like any verified endpoint: a running controller with that UID is
        repointed (firing its address-changed callback), and a new or moved
        unclaimed endpoint is notified.

        Raises:
            UnpairedBridgeError: If the probed UID is the unpaired placeholder;
                the endpoint is not recorded.

        """
        addresses = await self._resolve(host)
        for address in addresses:
            owned = self._entry_by_host(
                address, _State.CLAIMING, _State.CLAIMED, _State.KNOWN
            )
            if owned is not None:
                return owned.endpoint
        for address in addresses:
            pending = self._verifying_host(address)
            if pending is not None and (verified := await self._await_verify(pending)):
                return verified

        probed = await self._probe_first(addresses)
        if probed is None:
            return None
        raise_if_placeholder_uid(probed.endpoint.uid)
        self._apply_verified(probed.endpoint)
        return probed.endpoint

    async def discover_by_uid(self, uid: str) -> ControllerEndpoint | None:
        """Broadcast IASD, wait for *uid*, and HTTP-verify the response.

        User-initiated lookup. Cache hits skip re-probe.

        Raises:
            UnpairedBridgeError: If *uid* is the unpaired placeholder (no I/O).
            ControllerAlreadyClaimedError: If a controller with *uid* is created
                or being created when the call starts.

        """
        raise_if_placeholder_uid(uid)
        self._ensure_uid_available(uid)
        if (known := self._entry(uid, _State.KNOWN)) is not None:
            return known.endpoint
        if (seen := self._entry(uid, _State.SEEN)) is not None and (
            verified := await self._await_verify(seen.endpoint)
        ):
            return verified
        endpoint = await self._scan_for_uid(uid)
        if endpoint is not None:
            self._apply_verified(endpoint)
        return endpoint

    async def _scan_for_uid(self, uid: str) -> ControllerEndpoint | None:
        """Broadcast IASD, wait for *uid*, and HTTP-verify it (no caching).

        Joins a verify already in flight for the reply, else probes directly so a
        claiming entry is left untouched.
        """
        async with self._discover_lock:
            if (known := self._entry(uid, _State.KNOWN)) is not None:
                return known.endpoint

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
            endpoint = ControllerEndpoint(uid=uid, host=host)
            entry = self._endpoints.get(uid)
            if entry is not None and entry.state in (_State.SEEN, _State.KNOWN):
                if entry.state is _State.KNOWN and entry.endpoint == endpoint:
                    return endpoint
                if entry.candidate == endpoint:
                    return await self._await_verify(endpoint)

            probed = await self._probe(host, expected_uid=uid)
            return probed.endpoint if probed is not None else None

    async def discover_all(self) -> list[ControllerEndpoint]:
        """Broadcast IASD, collect replies, and HTTP-verify each new endpoint.

        Invokes ``on_endpoint_discovered`` once per verified uid for this call.
        ASPort during the wait uses the normal discovery notify path (verified
        new / host-change only); already-verified endpoints are not re-probed
        but are notified once so a user-initiated scan still fans out when
        nothing new arrived over UDP (e.g. already-known same-host).
        """
        async with self._discover_lock:
            notified: set[str] = set()
            self._discover_all_notified = notified
            collector: dict[str, str] = {
                uid: endpoint.host
                for uid, endpoint in self._endpoints_in(_State.KNOWN).items()
            }
            self._scan_collector = collector
            try:
                try:
                    await self.scan()
                    await asyncio.sleep(SCAN_TIMEOUT)
                finally:
                    self._scan_collector = None
                return await self._verify_collected(collector, notified)
            finally:
                self._discover_all_notified = None

    async def _verify_collected(
        self, collector: dict[str, str], notified: set[str]
    ) -> list[ControllerEndpoint]:
        """Verify collected endpoints, notifying known ones not yet notified."""
        verified: list[ControllerEndpoint] = []
        for uid, host in collector.items():
            endpoint = ControllerEndpoint(uid=uid, host=host)
            if self._entry(uid, _State.CLAIMING, _State.CLAIMED) is not None:
                continue
            known = self._entry(uid, _State.KNOWN)
            if known is None or known.endpoint != endpoint:
                # A successful verify notifies via _emit_endpoint_discovered.
                if await self._await_verify(endpoint) is None:
                    continue
            verified.append(endpoint)
            if uid not in notified:
                self._emit_endpoint_discovered(endpoint)
        return verified

    async def create_controller(
        self,
        uid: str,
        host: str,
        *,
        on_address_changed: Callable[[ControllerEndpoint], None] | None = None,
    ) -> Controller:
        """Create and initialize a controller at the given address.

        Preferred entry point after :func:`create_discovery`. One-shot per
        UID. *host* may be an IPv4 address or a name; a name is resolved and
        the controller runs on the IP that answers. HTTP probe is the trust
        boundary; callers must handle the device being unreachable even after a
        recent discovery notify.

        Raises:
            UnpairedBridgeError: If *uid* is the unpaired placeholder (no I/O).
            ControllerAlreadyClaimedError: If a controller with *uid* already exists.
            ConnectionError: If the controller cannot be reached after
                address fallback.
            ControllerCommandError: If the device rejects the protocol request.
            ResponseDecodeError: If the device returns malformed data.
            KeyError: If a required field is missing from a device response.

        """
        raise_if_placeholder_uid(uid)
        self._ensure_uid_available(uid)
        prior = self._entry(uid, _State.KNOWN)
        # Reserve the UID before the first await so overlapping calls fail fast.
        self._endpoints[uid] = _Entry(
            _State.CLAIMING, ControllerEndpoint(uid=uid, host=host)
        )
        pending_address_changed: ControllerEndpoint | None = None
        fallback: ControllerEndpoint | None = None

        try:
            try:
                controller = await self._connect_controller_at(
                    uid, host, on_address_changed=on_address_changed
                )
            except ConnectionError:
                if prior is not None:
                    fallback = prior.endpoint
                else:
                    fallback = await self._scan_for_uid(uid)
                if fallback is None or fallback.host == host:
                    raise
                pending_address_changed = fallback
                controller = await self._connect_controller_at(
                    uid,
                    fallback.host,
                    on_address_changed=on_address_changed,
                )
        except BaseException:
            if fallback is not None:
                self._endpoints[uid] = _Entry(_State.KNOWN, fallback)
            elif prior is not None:
                self._endpoints[uid] = prior
            else:
                del self._endpoints[uid]
            raise

        if pending_address_changed is not None and on_address_changed is not None:
            self.schedule_address_changed(on_address_changed, pending_address_changed)
        return controller

    def schedule_address_changed(
        self,
        callback: Callable[[ControllerEndpoint], None],
        endpoint: ControllerEndpoint,
    ) -> None:
        """Schedule a one-shot address-changed callback after the caller returns."""
        if (claimed := self._entry(endpoint.uid, _State.CLAIMED)) is not None:
            claimed.endpoint = endpoint
        asyncio.get_running_loop().create_task(
            _invoke_address_changed(callback, endpoint)
        )

    async def _connect_controller_at(
        self,
        uid: str,
        host: str,
        *,
        on_address_changed: Callable[[ControllerEndpoint], None] | None,
    ) -> Controller:
        probed = await self._probe_first(await self._resolve(host), expected_uid=uid)
        if probed is None:
            raise ConnectionError(f"Unable to connect to controller {uid} at {host}")

        controller = await self._controller_cls.create(
            self,
            probe=probed,
            on_address_changed=on_address_changed,
        )
        self._claim(controller)
        return controller

    def _controller_closed(self, controller: Controller) -> None:
        """Release a closed controller back to the unclaimed endpoint cache."""
        entry = self._entry(controller.device_uid, _State.CLAIMED)
        if entry is None or entry.controller is not controller:
            return
        self._endpoints[controller.device_uid] = _Entry(
            _State.KNOWN,
            ControllerEndpoint(uid=controller.device_uid, host=controller.device_ip),
        )

    async def _resolve(self, host: str) -> list[str]:
        """Return the IPv4 addresses for *host*; empty if it cannot be resolved."""
        with suppress(ValueError):
            return [str(IPv4Address(host))]
        try:
            async with asyncio.timeout(Controller.REQUEST_TIMEOUT):
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host, None, family=socket.AF_INET, type=socket.SOCK_STREAM
                )
        except OSError, TimeoutError:
            _LOG.debug("Unable to resolve %s", host, exc_info=True)
            return []
        return list(dict.fromkeys(str(info[4][0]) for info in infos))

    async def _probe_first(
        self, addresses: list[str], *, expected_uid: str | None = None
    ) -> ControllerProbe | None:
        """Probe *addresses* concurrently; return the first controller that answers.

        Concurrent so a dead address (e.g. a stale DNS record) doesn't cost a
        full timeout before the next is tried. Two addresses can be the same
        bridge, which then sees overlapping requests it handles poorly; accepted
        because a name resolving to several addresses is rare.
        """
        if not addresses:
            return None
        if len(addresses) == 1:
            return await self._probe(addresses[0], expected_uid=expected_uid)
        tasks = [
            asyncio.ensure_future(self._probe(address, expected_uid=expected_uid))
            for address in addresses
        ]
        try:
            for next_done in asyncio.as_completed(tasks):
                if (probed := await next_done) is not None:
                    return probed
            return None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe(
        self, host: str, *, expected_uid: str | None = None
    ) -> ControllerProbe | None:
        """Probe controller; prefer useful V2 SystemV2, else V1 SystemSettings.

        Returns None when unreachable, malformed, or not *expected_uid*.
        """
        probed = await self._probe_any(host)
        if probed is None:
            return None
        if expected_uid is not None and probed.endpoint.uid != expected_uid:
            return None
        return probed

    async def _probe_any(self, host: str) -> ControllerProbe | None:
        """Probe *host* for whichever controller answers."""
        session = self._session
        if session is None:
            return None
        if (v2_probe := await _probe_v2(session, host)) is not None:
            return v2_probe

        try:
            async with session.get(
                f"http://{host}/SystemSettings",
                timeout=aiohttp.ClientTimeout(total=Controller.REQUEST_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    return None
                try:
                    data = await response.json(content_type=None)
                except json.JSONDecodeError:
                    text = await response.text()
                    if len(text) < 4 or text[-4:] != "{OK}":
                        return None
                    try:
                        data = json.loads(text[:-4])
                    except json.JSONDecodeError:
                        return None
        except TimeoutError, aiohttp.ClientError, OSError, UnicodeDecodeError:
            return None

        if not isinstance(data, dict):
            return None
        v1_uid = data.get("AirStreamDeviceUId")
        if not isinstance(v1_uid, str) or not v1_uid:
            return None
        return ControllerProbe(
            endpoint=ControllerEndpoint(uid=v1_uid, host=host),
            system_settings=data,
            read_api="v1",
        )

    async def close(self) -> None:
        """Stop discovery, close the UDP transport, and cancel tracked tasks."""
        current = asyncio.current_task()
        if self._close_task and self._close_task is not current:
            with suppress(CancelledError):
                await self._close_task
            return
        _LOG.info("Close called on discovery service.")
        self._close_task = current
        transport = self._transport
        if transport is not None:
            # Clear _transport first: None means "closing or closed", so
            # senders short-circuit instead of writing to a closing socket.
            self._transport = None
            # DatagramTransport.close() only schedules sock release via
            # call_soon(_call_connection_lost). Wait for connection_lost so a
            # following create_discovery can bind :7005.
            transport.close()
            try:
                async with asyncio.timeout(TRANSPORT_CLOSE_TIMEOUT):
                    await self._transport_released.wait()
            except TimeoutError:
                # A failed Windows Proactor send can leave connection_lost
                # unscheduled. Abort pending I/O instead of hanging shutdown.
                _LOG.debug("Discovery transport did not close; aborting it")
                transport.abort()

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
        self._transport = None
        self._transport_released.set()
        if not self._close_task:
            _LOG.error("Connection Lost unexpectedly: %s", repr(exc))
            self.create_task(self.close())

    @property
    def is_closed(self) -> bool:
        """Return whether the discovery service is closed."""
        if self._transport:
            return self._transport.is_closing()
        return self._close_task is not None

    def dump_state(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of discovery registry state.

        Does not send an IASD broadcast. Includes a ring buffer of the most
        recent inbound UDP datagrams (excluding our own IASD echo).
        """

        def _endpoints(state: _State) -> list[dict[str, str]]:
            return [
                {"uid": endpoint.uid, "host": endpoint.host}
                for endpoint in self._endpoints_in(state).values()
            ]

        recent_udp = [
            {
                **reply,
                "received_at": datetime.fromtimestamp(
                    reply["received_at"], tz=UTC
                ).isoformat(),
            }
            for reply in self._recent_udp
        ]

        return {
            "closed": self.is_closed,
            "udp_bound": self._transport is not None and not self.is_closed,
            "claiming": _endpoints(_State.CLAIMING),
            "claimed": _endpoints(_State.CLAIMED),
            "known": _endpoints(_State.KNOWN),
            "seen": _endpoints(_State.SEEN),
            "recent_udp": recent_udp,
        }

    @property
    def session(self) -> ClientSession | None:
        """Return the aiohttp session used for HTTP requests."""
        return self._session

    def _on_error_received(self, exc: Exception) -> None:
        # Expected when broadcast is blocked (firewall / no permission) or the
        # interface is down; scan just finds nothing. Keep unexpected errors loud.
        if isinstance(exc, OSError):
            _LOG.debug("UDP error ignored: %s", exc)
            return
        _LOG.warning("Error passed and ignored to error_received: %s", exc)

    def _on_datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        _LOG.debug("Datagram Received %s", data)
        if self._close_task:
            return
        self._process_datagram(data, addr)

    def _process_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if data == DISCOVERY_MSG:
            return
        self._record_udp_datagram(data, addr)
        if data in (CHANGED_SYSTEM, CHANGED_ZONES, CHANGED_SCHEDULES):
            _LOG.debug("Ignoring push notification %s from %s", data, addr)
            return
        self._discovery_received(data)

    def _record_udp_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Append an inbound UDP datagram to the diagnostics ring buffer."""
        try:
            message = data.decode()
        except UnicodeDecodeError:
            message = data.decode(errors="replace")
        entry: dict[str, Any] = {
            "source_ip": addr[0],
            "source_port": addr[1],
            "received_at": time.time(),
            "message": message,
        }
        endpoint = self._parse_datagram(data)
        if endpoint is not None:
            entry["uid"] = endpoint.uid
            entry["host"] = endpoint.host
            parts = message.split(",")
            entry["tags"] = parts[3:] if len(parts) > 3 else []
        self._recent_udp.append(entry)

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

    def _discovery_received(self, data: bytes) -> None:
        """Handle passive ASPort: HTTP-verify new hosts before acting on them."""
        endpoint = self._parse_datagram(data)
        if endpoint is None:
            _LOG.warning("Invalid Message Received: %s", data.decode(errors="replace"))
            return

        if endpoint.uid == PLACEHOLDER_DEVICE_UID:
            return

        if self._scan_collector is not None:
            self._scan_collector[endpoint.uid] = endpoint.host

        entry = self._endpoints.get(endpoint.uid)
        if entry is not None and entry.state is _State.CLAIMING:
            return
        if (
            entry is not None
            and entry.state in (_State.KNOWN, _State.CLAIMED)
            and entry.endpoint == endpoint
        ):
            return
        self._verify_endpoint(endpoint)


async def _probe_v2(session: ClientSession, host: str) -> ControllerProbe | None:
    """Probe *host* for a useful SystemV2 that converts to settings."""
    v2_hit = await v2_mod.fetch_useful_system(
        session, host, timeout=Controller.REQUEST_TIMEOUT
    )
    if v2_hit is None:
        return None
    raw, use_ct = v2_hit
    uid = cast(str, raw["AirStreamDeviceUId"])
    try:
        settings = v2_mod.system_v2_to_settings(
            cast(dict[str, Any], raw["SystemV2"]), uid
        )
    except TypeError, ValueError, OverflowError:
        _LOG.debug("Malformed SystemV2 from %s; trying V1", host, exc_info=True)
        return None
    return ControllerProbe(
        endpoint=ControllerEndpoint(uid=uid, host=host),
        system_settings=settings,
        read_api="v2",
        v2_use_content_type=use_ct,
    )


def _clear_discovery_global(service: DiscoveryService) -> None:
    global _active_discovery  # noqa: PLW0603
    if _active_discovery is service:
        _active_discovery = None


async def _invoke_address_changed(
    callback: Callable[[ControllerEndpoint], None],
    endpoint: ControllerEndpoint,
) -> None:
    with LogExceptions("on_address_changed"):
        callback(endpoint)


async def create_discovery(
    *,
    on_endpoint_discovered: Callable[[ControllerEndpoint], None] | None = None,
    session: ClientSession | None = None,
) -> DiscoveryService:
    """Create the process-global discovery service.

    Use :meth:`DiscoveryService.create_controller`
    (and the ``discover_*`` helpers) on the returned service; do not construct
    :class:`~pizone.controller.Controller` instances directly.

    Args:
        on_endpoint_discovered: Called for each newly discovered endpoint,
            only after it answers an HTTP verify.
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
    try:
        await service.start_discovery()
    except BaseException:
        # close() clears the global when it finishes; clear again if close never ran.
        with suppress(BaseException):
            await service.close()
        _clear_discovery_global(service)
        raise
    return service
