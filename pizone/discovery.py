"""iZone device discovery."""

import asyncio
import logging
from asyncio import (
    CancelledError,
    Condition,
    DatagramProtocol,
    DatagramTransport,
    Task,
)
from contextlib import suppress
from typing import Any

import netifaces  # type: ignore
from aiohttp import ClientSession

from .controller import Controller
from .zone import Zone

DISCOVERY_MSG = b"IASD"  # cspell:disable-line
DISCOVERY_PORT = 12107

UPDATE_PORT = 7005
CHANGED_SYSTEM = b"iZoneChanged_System"
CHANGED_ZONES = b"iZoneChanged_Zones"
CHANGED_SCHEDULES = b"iZoneChanged_Schedules"

DISCOVERY_SLEEP = 5.0 * 60.0
DISCOVERY_RESCAN = 20.0
RESCAN_COOLDOWN = 5.0

_LOG = logging.getLogger("pizone.discovery")


class LogExceptions:
    """Utility context manager to log and discard exceptions"""

    def __init__(self, func: str) -> None:
        self.func = func

    def __enter__(self) -> "LogExceptions":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if exc_type:
            _LOG.error(
                "Exception ignored when calling listener %s", self.func, exc_info=True
            )
        return True


class Listener:
    """Base class for listeners for iZone updates"""

    def controller_discovered(self, ctrl: Controller) -> None:
        """
        New controller discovered. This will also be called for all
        existing controllers if a new listener is registered
        """

    def controller_disconnected(self, ctrl: Controller, ex: Exception) -> None:
        """
        Connection lost to controller. Exception argument will show reason why.
        """

    def controller_reconnected(self, ctrl: Controller) -> None:
        """
        Reconnected to controller.
        """

    def controller_update(self, ctrl: Controller) -> None:
        """Called when a system update message is received from the controller.
        Controller data will be set to new value.
        """

    def zone_update(self, ctrl: Controller, zone: Zone) -> None:
        """Called when a zone update message is received from the controller
        Zone data will be set to new value.
        """

    def power_update(self, ctrl: Controller) -> None:
        """Called when the power monitor updates."""


class DiscoveryService:
    """Discovery service: manages controller registry, listener fanout, and UDP scanning."""

    def __init__(self, session: ClientSession | None = None) -> None:
        """Start the discovery protocol using the supplied loop.

        raises:
            RuntimeError: If attempted to start the protocol when it is
                          already running.
        """
        self._controllers: dict[str, Controller] = {}
        self._disconnected: set[str] = set()
        self._listeners: list[Listener] = []
        self._close_task: Task | None = None

        _LOG.info("Starting discovery protocol")
        self._session = session
        self._own_session = session is None

        self._transport: DatagramTransport | None = None

        self._scan_condition = Condition()
        self._last_rescan_time: float = 0.0

        self._tasks: list[Task] = []

        _srv = self

        class _EventCoordinator(Listener):
            """Fan-out adapter: dispatches controller/zone events to all registered listeners."""

            # pylint: disable=protected-access

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

    # Async context manager interface
    async def __aenter__(self) -> "DiscoveryService":
        await self.start_discovery()
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        await self.close()

    def _task_done_callback(self, task: Task) -> None:
        try:
            if task.exception():
                _LOG.exception("Uncaught exception", exc_info=task.exception())
        except CancelledError:
            pass
        self._tasks.remove(task)

    # managing the task list.
    def create_task(self, coro: Any) -> Task:
        """Create a task in the event loop. Keeps track of created tasks."""
        task: Task = asyncio.get_running_loop().create_task(coro)
        self._tasks.append(task)

        task.add_done_callback(self._task_done_callback)
        return task

    # Listeners.
    def add_listener(self, listener: Listener) -> None:
        """Add a discovered listener.

        All existing controllers will be passed to the listener."""
        self._listeners.append(listener)

        def callback() -> None:
            for controller in self._controllers.values():
                listener.controller_discovered(controller)

        asyncio.get_running_loop().call_soon(callback)

    def remove_listener(self, listener: Listener) -> None:
        """Remove a listener"""
        self._listeners.remove(listener)

    # Non-context versions of starting.
    async def start_discovery(self) -> None:
        """Start discovery protocol. Creates UDP socket and begins scanning for devices."""
        if self._own_session:
            self._session = ClientSession()

        _svc = self

        class _UDPTransport(DatagramProtocol):
            # pylint: disable=protected-access
            def connection_made(self, transport: DatagramTransport) -> None:  # type: ignore
                _svc._on_connection_made(transport)

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

    # Private callbacks invoked by _UDPTransport.
    def _on_connection_made(self, transport: DatagramTransport) -> None:
        if self._close_task:
            transport.close()
            return
        assert not self._transport, "Another connection made"

        self._transport = transport
        self.create_task(self._scan_loop())

    def _get_broadcasts(self) -> Any:
        for ifaddr in map(netifaces.ifaddresses, netifaces.interfaces()):
            inetaddrs = ifaddr.get(netifaces.AF_INET)
            if not inetaddrs:
                continue
            for inetaddr in inetaddrs:
                broadcast = inetaddr.get("broadcast")
                if broadcast:
                    yield broadcast

    def _send_broadcasts(self) -> None:
        assert self._transport is not None, "Discovery transport is not ready"
        for broadcast in self._get_broadcasts():
            _LOG.debug("Sending discovery message to addr %s", broadcast)
            self._transport.sendto(DISCOVERY_MSG, (broadcast, DISCOVERY_PORT))

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
            except asyncio.TimeoutError:
                pass

            if self._close_task:
                return

    async def _rescan(self) -> None:
        async with self._scan_condition:
            self._scan_condition.notify()

    async def _maybe_rescan(self) -> None:
        """Trigger a rescan only if outside the cool-down window."""
        now = asyncio.get_running_loop().time()
        if now - self._last_rescan_time >= RESCAN_COOLDOWN:
            self._last_rescan_time = now
            await self._rescan()

    async def fetch_controller(
        self, uid: str, timeout: float | None = None
    ) -> Controller | None:
        """Return the controller with *uid*, optionally waiting up to *timeout* seconds."""
        if uid in self._controllers:
            return self._controllers[uid]
        if timeout is None:
            return None

        ready = asyncio.Event()

        class _WaitForUidListener(Listener):
            def controller_discovered(self, ctrl: Controller) -> None:  # noqa: N805
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

    async def fetch_controllers(
        self, timeout: float | None = None
    ) -> dict[str, Controller]:
        """Return all known controllers, optionally waiting for discovery to settle."""
        if timeout is None:
            return dict(self._controllers)

        await self._maybe_rescan()
        await asyncio.sleep(timeout)
        return dict(self._controllers)

    # Closing the connection
    async def close(self) -> None:
        """Close the transport"""
        if self._close_task:
            await self._close_task
            return
        _LOG.info("Close called on discovery service.")
        self._close_task = asyncio.current_task()
        if self._transport:
            self._transport.close()

        for i in self._tasks:
            i.cancel()

        if self._own_session and self._session:
            await self._session.close()

        await asyncio.wait(self._tasks)

    def _on_connection_lost(self, exc: Exception | None) -> None:
        _LOG.debug("Connection Lost")
        if not self._close_task:
            _LOG.error("Connection Lost unexpectedly: %s", repr(exc))
            asyncio.get_running_loop().create_task(self.close())

    @property
    def is_closed(self) -> bool:
        """Check if the discovery service is closed."""
        if self._transport:
            return self._transport.is_closing()
        return self._close_task is not None

    @property
    def session(self) -> Any:
        """Return the aiohttp session used for HTTP requests."""
        return self._session

    def _on_error_received(self, _: Exception) -> None:
        _LOG.warning("Error passed and ignored to error_received", exc_info=True)

    def _find_by_addr(self, addr: tuple[str, int]) -> Controller | None:
        for _, ctrl in self._controllers.items():
            if ctrl.device_ip == addr[0]:
                return ctrl
        return None

    async def _wrap_update(self, coro: Any) -> None:
        try:
            await coro
        except ConnectionError:
            _LOG.warning(
                "Unable to complete %s due to connection error", coro, exc_info=True
            )

    def _on_datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        _LOG.debug("Datagram Received %s", data)
        if self._close_task:
            return
        self._process_datagram(data, addr)

    def _process_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if data in (DISCOVERY_MSG, CHANGED_SCHEDULES):
            # ignore
            pass
        elif data == CHANGED_SYSTEM:
            ctrl = self._find_by_addr(addr)
            if ctrl:
                # pylint: disable=protected-access
                self.create_task(self._wrap_update(ctrl._refresh_system()))
        elif data == CHANGED_ZONES:
            ctrl = self._find_by_addr(addr)
            if ctrl:
                # pylint: disable=protected-access
                self.create_task(self._wrap_update(ctrl._refresh_zones()))
        else:
            self._discovery_received(data)

    def _discovery_received(self, data: bytes) -> None:
        message = data.decode().split(",")
        if (
            len(message) < 3
            or message[0] != "ASPort_12107"
            or (len(message) >= 4 and {"iZone", "iZoneV2"}.isdisjoint(message[3:]))
        ):
            _LOG.warning("Invalid Message Received: %s", data.decode())
            return

        device_uid = message[1].split("_")[1]
        device_ip = message[2].split("_")[1]

        # pylint: disable=protected-access
        if device_uid not in self._controllers:
            # Create new controller.
            # We don't have to set the loop here since it's set for
            # the thread already.
            is_v2 = len(message) >= 4 and "iZoneV2" in message[3:]
            is_ipower = len(message) >= 4 and "iPower" in message[3:]
            controller = self._create_controller(
                device_uid, device_ip, is_v2, is_ipower
            )

            async def initialize_controller() -> None:
                try:
                    await controller._initialize()  # noqa: E501
                except ConnectionError as ex:
                    _LOG.warning(
                        "Can't connect to discovered server at IP '%s' exception: %s",
                        device_ip,
                        repr(ex),
                    )
                    return

                self._controllers[device_uid] = controller
                self._event_coordinator.controller_discovered(controller)

            self.create_task(initialize_controller())
        else:
            controller = self._controllers[device_uid]
            controller._refresh_address(device_ip)

    def _create_controller(
        self, device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> Controller:
        return Controller(
            self,
            self._event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )


def discovery(
    *listeners: Listener, session: ClientSession | None = None
) -> DiscoveryService:
    """Create discovery service. Returned object is a asynchronous
    context manager so can be used with 'async with' statement.
    Alternately call start_discovery or start_discovery_async to commence
    the discovery process."""
    service = DiscoveryService(session=session)
    for listener in listeners:
        service.add_listener(listener)
    return service
