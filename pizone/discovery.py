"""iZone device discovery."""
import socket
import asyncio
import socket

from datetime import timedelta
from typing import Dict, Iterable
from asyncio import DatagramProtocol, DatagramTransport, Future, AbstractEventLoop, Event
from threading import Condition

import pizone
from .utils import CoolDown
from .controller import Controller
from .async_utils import get_event_loop

DISCOVERY_MSG = b'IASD'

UDP_PORT = 12107

DISCOVERY_ADDRESS = '<broadcast>'
DISCOVERY_TIMEOUT = timedelta(seconds=2)

_DISCOVERED: Dict[str, Controller] = {}

@CoolDown(10000)
def scan(force=False) -> bool:
    """Scan network for iZone devices.

    Returns:
        True if new devices discovered.
    """
    loop: AbstractEventLoop = get_event_loop()
    future = asyncio.run_coroutine_threadsafe(scan_async(), loop)
    result = future.result()
    return result
 
async def scan_async(self) -> None:
    """Scan network for iZone devices. Asynchronous version.

    Returns:
        True if new devices discovered.
    """

    class ScanProtocol(DatagramProtocol):
        transport:DatagramTransport
        sock:socket
        complete:Future
        updated:bool

        def __init__(self, loop:AbstractEventLoop, complete:Future):
            self.complete = complete
            self.loop = loop

        def timeout(self):
            self.transport.close()

        def connection_made(self, transport: DatagramTransport):
            self.transport = transport
            self.updated = False
            sock:socket.socket = transport.get_extra_info('socket')
            self.transport.sendto(DISCOVERY_MSG, (DISCOVERY_ADDRESS, UDP_PORT))
            loop.call_later(DISCOVERY_TIMEOUT.seconds, self.timeout)

        def datagram_received(self, data, addr):
            if data == DISCOVERY_MSG:
                return
            message = data.decode().split(',')
            if len(message) < 4 or message[0] != 'ASPort_12107' or message[3] != 'iZone':
                print("Invalid Message Received:", data.decode())
                return
            if message[3] != 'iZone':
                return

            device_uid = message[1].split('_')[1]
            address = message[2].split('_')[1]

            if device_uid not in _DISCOVERED:
                _DISCOVERED[device_uid] = Controller(device_uid=device_uid, device_ip=address)
                self.updated = True
            else:
                print(f"Redestination {device_uid} to {address}")
                disco = _DISCOVERED[device_uid] #pylint: disable=protected-access
                if disco and disco._ip != address: #pylint: disable=protected-access
                    disco._ip = address #pylint: disable=protected-access

        def error_received(self, exc):
            print('Error received:', exc)
            self.transport.close()

        def connection_lost(self, exc):
            self.complete.set_result(self.updated)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(('', UDP_PORT+1))

        loop: AbstractEventLoop = asyncio.get_event_loop()
        complete: Future = loop.create_future()

        connect = loop.create_datagram_endpoint(
            lambda: ScanProtocol(loop, complete),
            sock=sock)
        await connect
        await complete

    return complete.result()

def controllers_all() -> Iterable[Controller]:
    """Scan network for all iZone controllers."""
    scan()
    return _DISCOVERED.values()

def controller_by_uid(uid) -> Controller:
    """Scan network and return a controller matching a particular id, or None if none found"""
    scan()
    return _DISCOVERED[uid]

def controller_single() -> Controller:
    """Get a singleton controller.
    raises:
        ConnectionAbortedError: If multiple controllers found.
        ConnectionRefusedError: If no controllers are found.
    """
    scan()
    if len(_DISCOVERED) > 1:
        raise ConnectionAbortedError('Multiple iZone instances discovered on network')
    if not _DISCOVERED:
        raise ConnectionRefusedError('No iZone device found')
    return next(iter(_DISCOVERED.values()))
    