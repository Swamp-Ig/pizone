"""iZone device discovery."""
import socket

from datetime import timedelta

from .utils import CoolDown
from .controller import Controller

DISCOVERY_MSG = b'IASD'

UDP_PORT = 12107

DISCOVERY_ADDRESS = '<broadcast>'
DISCOVERY_TIMEOUT = timedelta(seconds=2)

_DISCOVERED = {}

@CoolDown(10000)
def scan(force=False) -> bool:
    """Scan network for iZone devices.

    Returns:
        List of all devices discovered.
    """
    updated = False

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(DISCOVERY_TIMEOUT.seconds)

    try:
        sock.sendto(DISCOVERY_MSG, (DISCOVERY_ADDRESS, UDP_PORT))
        while True:
            try:
                data, (address, _) = sock.recvfrom(1024)

                message = data.decode().split(',')
                if len(message) < 4 or message[0] != 'ASPort_12107' or message[3] != 'iZone':
                    print("Invalid Message Received:", data.decode())
                    continue
                if message[3] != 'iZone':
                    continue

                device_uid = message[1].split('_')[1]
                address = message[2].split('_')[1]

                if device_uid not in _DISCOVERED:
                    _DISCOVERED[device_uid] = Controller(device_uid=device_uid, device_ip=address)
                    updated = True
                else:
                    disco = _DISCOVERED[device_uid] #pylint: disable=protected-access
                    if disco and disco._ip != address: #pylint: disable=protected-access
                        disco._ip = address #pylint: disable=protected-access
            except socket.timeout:
                break
    finally:
        sock.close()

    return updated

def controllers_all() -> [Controller]:
    """Scan network for all iZone controllers."""
    scan()
    return _DISCOVERED.values

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
    