"""iZone device discovery."""

import asyncio
import logging
import socket
from abc import abstractmethod
from asyncio import (AbstractEventLoop, Condition, DatagramProtocol,
                     DatagramTransport, Future, Task)
from logging import Logger
from contextlib import contextmanager
from typing import Dict, List, Optional

from aiohttp import ClientSession

import netifaces

from .controller import Controller
from .zone import Zone

DISCOVERY_MSG = b'IASD'
DISCOVERY_PORT = 12107

UPDATE_PORT = 7005
CHANGED_SYSTEM = b'iZoneChanged_System'
CHANGED_ZONES = b'iZoneChanged_Zones'
CHANGED_SCHEDULES = b'iZoneChanged_Schedules'

DISCOVERY_TIMEOUT = 2 # type: float
DISCOVERY_SLEEP = 5*60 # type: float

_LOG = logging.getLogger('pizone.discovery') # type: Logger

class LogExceptions:
    """Utility context manager to log and discard exceptions"""
    def __init__(self, func: str) -> None:
        self.func = func

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            _LOG.exception("Exception ignored when calling listener %s", self.func)
        return True


class Listener:
    """Base class for listeners for iZone updates"""

    def controller_discovered(self, ctrl: Controller) -> None:
        """
        New controller discovered. This will also be called for all
        existing controllers if a new listener is registered
        """
        pass

    def controller_disconnected(self, ctrl: Controller, ex: Exception) -> None:
        """
        Connection lost to controller. Exception argument will show reason why.
        """
        pass

    def controller_reconnected(self, ctrl: Controller) -> None:
        """
        Reconnected to controller.
        """
        pass

    def controller_update(self, ctrl: Controller) -> None:
        """Called when a system update message is recieved from the controller.
        Controller data will be set to new value.
        """
        pass

    def zone_update(self, ctrl: Controller, zone: Zone) -> None:
        """Called when a zone update message is recieved from the controller
        Zone data will be set to new value.
        """
        pass


class AbstractDiscoveryService:
    """Interface for discovery. This service is both a context manager, and an asynchronous
    context manager. When used in the context manager version, the start discovery and close
    will be called automatically when opening and closing the context respectively.
    """

    @abstractmethod
    def add_listener(self, listener: Listener) -> None:
        """Add a listener. All existing controllers will be passed to the listener."""
        pass

    @abstractmethod
    def remove_listener(self, listener: Listener) -> None:
        """Remove a listener"""
        pass

    @abstractmethod
    async def start_discovery(self) -> None:
        """Async version to start discovery.
        Will return once discovery is started, but before any controllers are found."""

    @abstractmethod
    async def rescan(self) -> None:
        """Trigger rescan for new controllers / update IP addresses of existing controllers.
        Returns immediately, listener will be called with any new controllers or if reconnected.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Stop discovery and close all connections"""
        pass

    @property
    def is_closed(self) -> bool:
        """Return true if closed"""
        pass

    @property
    def controllers(self) -> Dict[str, Controller]:
        """Dictionary of all the currently discovered controllers"""
        pass

class DiscoveryService(AbstractDiscoveryService, DatagramProtocol, Listener):
    """Discovery protocol class. Not for external use."""

    def __init__(self, loop: AbstractEventLoop = None,
                 session: ClientSession = None) -> None:
        """Start the discovery protocol using the supplied loop.
        raises:
            RuntimeError: If attempted to start the protocol when it is already running.
        """
        self._controllers = {} # type: Dict[str, Controller]
        self._listeners = [] # type: List[Listener]
        self._closing = False

        _LOG.info("Starting discovery protocol")
        if not loop:
            if session:
                self.loop = session.loop
            else:
                self.loop = asyncio.get_event_loop()
        else:
            self.loop = loop

        if not session:
            self.session = ClientSession(loop=self.loop)
            self._own_session = True
        else:
            assert session.loop is loop, "Passed client session does not share the same loop"
            self.session = session
            self._own_session = False

        self._transport = None # type: Optional[DatagramTransport]

        self._scan_condition = Condition(loop=self.loop) # type: Condition

        self._tasks = [] # type: List[Future]

    # Async context manager interface
    async def __aenter__(self) -> AbstractDiscoveryService:
        await self.start_discovery()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    def _task_done_callback(self, task):
        #_LOG.debug("Task done %s", task)
        if task.exception():
            _LOG.exception("Uncaught exception", exc_info=task.exception())
        self._tasks.remove(task)

    # managing the task list.
    def create_task(self, coro) -> Task:
        """Create a task in the event loop. Keeps track of created tasks."""
        task = self.loop.create_task(coro) # type: Task
        self._tasks.append(task)

        task.add_done_callback(self._task_done_callback)
        return task

    # Listeners.
    def add_listener(self, listener: Listener) -> None:
        """Add a discovered listener. All existing controllers will be passed to the listener."""
        self._listeners.append(listener)
        def callback():
            for controller in self._controllers.values():
                listener.controller_discovered(controller)
        self.loop.call_soon(callback)

    def remove_listener(self, listener: Listener) -> None:
        """Remove a listener"""
        self._listeners.remove(listener)

    def controller_discovered(self, ctrl: Controller) -> None:
        _LOG.info("New controller found: id=%s ip=%s", ctrl.device_uid, ctrl.device_ip)
        for listener in self._listeners:
            with LogExceptions("controller_discovered"):
                listener.controller_discovered(ctrl)

    def controller_disconnected(self, ctrl: Controller, ex: Exception) -> None:
        _LOG.warning("Connection to controller lost: id=%s ip=%s", ctrl.device_uid, ctrl.device_ip)
        for listener in self._listeners:
            with LogExceptions("controller_disconnected"):
                listener.controller_disconnected(ctrl, ex)

    def controller_reconnected(self, ctrl: Controller) -> None:
        _LOG.warning("Controller reconnected: id=%s ip=%s", ctrl.device_uid, ctrl.device_ip)
        for listener in self._listeners:
            with LogExceptions("controller_reconnected"):
                listener.controller_reconnected(ctrl)

    def controller_update(self, ctrl: Controller) -> None:
        for listener in self._listeners:
            with LogExceptions("controller_update"):
                listener.controller_update(ctrl)

    def zone_update(self, ctrl: Controller, zone: Zone) -> None:
        for listener in self._listeners:
            with LogExceptions("zone_update"):
                listener.zone_update(ctrl, zone)

    @property
    def controllers(self) -> Dict[str, Controller]:
        """Dictionary of all the currently discovered controllers"""
        return self._controllers

    # Non-context versions of starting.
    async def start_discovery(self) -> None:
        await self.loop.create_datagram_endpoint(lambda: self, 
            local_addr=('0.0.0.0', UPDATE_PORT),
            allow_broadcast=True)

    def connection_made(self, transport: DatagramTransport) -> None: # type: ignore
        if self._closing:
            transport.close()
            return
        assert not self._transport, "Another connection made"

        self._transport = transport
        self.create_task(self._scan_loop())

    async def _await_scan(self) -> None:
        async with self._scan_condition:
            await self._scan_condition.wait()

    def _get_broadcasts(self):
        for ifAddr in map(netifaces.ifaddresses, netifaces.interfaces()):
            inetAddrs = ifAddr.get(netifaces.AF_INET)
            if not inetAddrs:
                continue
            for inetAddr in inetAddrs:
                broadcast = inetAddr.get('broadcast')
                if broadcast:
                    yield broadcast

    async def _scan_loop(self) -> None:
        assert self._transport, "Should be impossible"
        
        while True:
            for broadcast in self._get_broadcasts():
                _LOG.debug("Sending discovery message to addr %s", broadcast)
                self._transport.sendto(DISCOVERY_MSG, (broadcast, DISCOVERY_PORT))
 
            try:
                await asyncio.wait_for(
                    asyncio.Task(self._await_scan()),
                    timeout=DISCOVERY_SLEEP)
            except asyncio.TimeoutError:
                pass

            if self._closing:
                return

    async def rescan(self) -> None:
        if self.is_closed:
            raise ConnectionError("Already closed")
        _LOG.debug("Manual rescan of controllers triggered.")
        async with self._scan_condition:
            self._scan_condition.notify()

    # Closing the connection
    async def close(self) -> None:
        """Close the transport"""
        if self._closing:
            return
        _LOG.info("Close called on discovery service.")
        assert self._transport, "Should be impossible"
        self._closing = True
        self._transport.close()

        async with self._scan_condition:
            self._scan_condition.notify()

        await asyncio.gather(*self._tasks)

        if self._own_session:
            await self.session.close()

    def connection_lost(self, exc):
        _LOG.debug("Connection Lost")
        if not self._closing:
            _LOG.exception("Connection Lost unexpectedly", exc_info=exc)
            self.create_task(self.close())

    @property
    def is_closed(self) -> bool:
        if self._transport:
            return self._transport.is_closing()
        return self._closing

    def error_received(self, exc):
        _LOG.exception("Exception raised and passed to error_recieved:", exc_info=exc)
        if not self._closing:
            self.create_task(self.close())

    def _find_by_addr(self, addr: str) -> Optional[Controller]:
        for _, ctrl in self._controllers.items():
            if ctrl.device_ip == addr[0]:
                return ctrl
        return None

    async def _wrap_update(self, coro):
        try:
            await coro
        except ConnectionError as ex:
            _LOG.warning("Unable to complete %s due to connection error", coro, exc_info=ex)

    def datagram_received(self, data, addr):
        _LOG.debug("Datagram Recieved %s", data)
        if self._closing:
            return

        if data in (DISCOVERY_MSG, CHANGED_SCHEDULES):
            # ignore
            pass
        elif data == CHANGED_SYSTEM:
            ctrl = self._find_by_addr(addr)
            if not ctrl:
                return
            self.create_task(self._wrap_update(ctrl._refresh_system())) # pylint: disable=protected-access
        elif data == CHANGED_ZONES:
            ctrl = self._find_by_addr(addr)
            if not ctrl:
                return
            self.create_task(self._wrap_update(ctrl._refresh_zones())) # pylint: disable=protected-access
        else:
            self._discovery_recieved(data)

    def _discovery_recieved(self, data):
        message = data.decode().split(',')
        if len(message) < 3 or message[0] != 'ASPort_12107':
            _LOG.warning("Invalid Message Received: %s", data.decode())
            return
        if len(message) > 3 and message[3] != 'iZone':
            return

        device_uid = message[1].split('_')[1]
        address = message[2].split('_')[1]

        if device_uid not in self._controllers:
            # Create new controller.
            # We don't have to set the loop here since it's set for
            # the thread already.
            controller = Controller(self, device_uid=device_uid, device_ip=address)

            def callback(task: Task):
                if task.exception():
                    _LOG.warning("Unable to connect to newly discovered controller", exc_info=task.exception())
                    return
                self._controllers[device_uid] = controller
                self.controller_discovered(controller)

            result = self.create_task(controller._initialize())  # type: Task  # pylint: disable=protected-access
            result.add_done_callback(callback)
        else:
            controller = self._controllers[device_uid]
            controller._refresh_address(address) # pylint: disable=protected-access

def discovery(*listeners: Listener,
              loop: AbstractEventLoop = None,
              session: ClientSession = None) -> AbstractDiscoveryService:
    """Create discovery service. Returned object is a asynchronous
    context manager so can be used with 'async with' statement.
    Alternately call start_discovery or start_discovery_async to commence the discovery
    process."""
    service = DiscoveryService(loop=loop, session=session)
    for listener in listeners:
        service.add_listener(listener)
    return service
