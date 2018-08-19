"""iZone device discovery."""

import asyncio
from asyncio import DatagramProtocol, DatagramTransport, AbstractEventLoop, Task
import logging
from logging import Logger
import socket
from typing import Dict, List, Callable, Optional

from aiohttp import ClientSession # type: ignore

from .controller import Controller

DISCOVERY_MSG = b'IASD'
DISCOVERY_PORT = 12107
DISCOVERY_ADDRESS = '<broadcast>'

UPDATE_PORT = 7005
CHANGED_SYSTEM = b'iZoneChanged_System'
CHANGED_ZONES = b'iZoneChanged_Zones'
CHANGED_SCHEDULES = b'iZoneChanged_Schedules'

DISCOVERY_TIMEOUT: float = 2
DISCOVERY_SLEEP: float = 5*60

DiscoveredListener = Callable[[Controller], None]

_LOG: Logger = logging.getLogger('pizone.discovery')

class DiscoveryProtocol(DatagramProtocol):
    """Discovery protocol class. Not for external use."""

    controllers: Dict[str, Controller]

    running: bool

    loop: AbstractEventLoop
    session: ClientSession

    transport: DatagramTransport
    sock: socket.socket

    scan_task: Task

    def __init__(self, loop: AbstractEventLoop = None,
                 session: ClientSession = None) -> None:
        """Start the discovery protocol using the supplied loop.
        raises:
            RuntimeError: If attempted to start the protocol when it is already running.
        """
        self.controllers = {}
        self.discovered_listeners: List[Callable] = []
        self.running = True

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
        else:
            assert session.loop is loop, "Passed client session does not share the same loop"
            self.session = session

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(('', UPDATE_PORT))
        self.loop.create_task(self.loop.create_datagram_endpoint(lambda: self, sock=self.sock))


    async def __aenter__(self) -> 'DiscoveryProtocol':
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close the transport"""
        self.scan_task.cancel()
        self.transport.close()

    def add_listener(self, listener: DiscoveredListener) -> None:
        """Add a discovered listener. All existing controllers will be passed to the listener."""
        def callback():
            self.discovered_listeners.append(listener)
            for controller in self.controllers.values():
                listener(controller)
        self.loop.call_soon(callback)

    def connection_made(self, transport):
        self.transport = transport
        assert self.loop
        if not self.session:
            self.session = ClientSession()
        self.scan_task = self.loop.create_task(self._scan_loop())

    async def _scan_loop(self) -> None:
        while True:
            _LOG.info("Sending discovery message")
            self.transport.sendto(DISCOVERY_MSG, (DISCOVERY_ADDRESS, DISCOVERY_PORT))
            # This can throw a cancelled error
            try:
                await asyncio.sleep(DISCOVERY_SLEEP, loop=self.loop)
            except asyncio.CancelledError:
                return

    def rescan(self) -> None:
        """Manually rescan for new controllers / update IP addresses of existing controllers."""
        _LOG.debug("Manual rescan of controllers triggered.")
        assert self.scan_task
        self.scan_task.cancel()
        self.scan_task = self.loop.create_task(self._scan_loop())

    def _find_by_addr(self, addr: str) -> Optional[Controller]:
        for _, ctrl in self.controllers.items():
            if ctrl.device_ip == addr:
                return ctrl
        return None

    def datagram_received(self, data, addr):
        if data in (DISCOVERY_MSG, CHANGED_SCHEDULES):
            # ignore
            pass
        elif data == CHANGED_SYSTEM:
            ctrl = self._find_by_addr(addr)
            if ctrl:
                self.loop.create_task(ctrl._refresh_system()) # pylint: disable=protected-access
        elif data == CHANGED_ZONES:
            ctrl = self._find_by_addr(addr)
            if ctrl:
                self.loop.create_task(ctrl._refresh_zones()) # pylint: disable=protected-access
        else:
            self._discovery_recieved(data)

    def _discovery_recieved(self, data):
        message = data.decode().split(',')
        if len(message) < 4 or message[0] != 'ASPort_12107' or message[3] != 'iZone':
            print("Invalid Message Received:", data.decode())
            return
        if message[3] != 'iZone':
            return

        device_uid = message[1].split('_')[1]
        address = message[2].split('_')[1]

        if device_uid not in self.controllers:
            # Create new controller.
            # We don't have to set the loop here since it's set for
            # the thread already.
            _LOG.info("New controller found: id=%s ip=%s", device_uid, address)
            controller = Controller(self, device_uid=device_uid, device_ip=address)

            def callback(task: Task):
                self.controllers[device_uid] = controller
                for listener in self.discovered_listeners:
                    self.loop.call_soon(listener, controller)

            result: Task = self.loop.create_task(controller.initialize(self.session))
            result.add_done_callback(callback)
        else:
            controller = self.controllers[device_uid]
            if controller.device_ip != address: # pylint: disable=protected-access
                _LOG.info("Device address update: id=%s  ip=%s", device_uid, address)
                self.loop.call_soon(controller.update_address, address)

    def error_received(self, exc):
        _LOG.exception("Exception raised and passed to error_recieved:", exc_info=exc)
        self.transport.close()

    def connection_lost(self, exc):
        pass


async def discovery(*listeners: DiscoveredListener,
                    loop: AbstractEventLoop = None,
                    session: ClientSession = None) -> None:
    """
    Entry point to the pizone connector.
    This coroutine will run indefinitely until the coroutine is cancelled.
    """
    async with DiscoveryProtocol(loop=loop, session=session) as protocol:
        for listener in listeners:
            protocol.add_listener(listener)
        while True:
            # sleep loop indefinitely. If the task running this coroutine is
            # cancelled, discovery will tidy up.
            try:
                await asyncio.sleep(5*60)
            except asyncio.CancelledError:
                return
