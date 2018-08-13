"""iZone device discovery."""
import socket

from datetime import timedelta

DISCOVERY_MSG = b'IASD'

UDP_PORT = 12107

DISCOVERY_ADDRESS = '<broadcast>'
DISCOVERY_TIMEOUT = timedelta(seconds=2)

class Discovered:
    """Controller information discovered by discovery process"""
    def __init__(self, addr, uid):
        self.addr = addr
        self.uid = uid

class Discovery:
    """Base class to discover iZone devices."""

    def __init__(self):
        """Initialize the iZone discovery."""
        self.entries = []

    def scan(self):
        """Scan the network."""
        self.update()

    def all(self):
        """Scan and return all found entries."""
        self.scan()
        return self.entries

    def update(self):
        """Scan network for iZone devices."""
        entries = []

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

                    cb_id = message[1].split('_')[1]
                    address = message[2].split('_')[1]

                    entries.append(Discovered(address, cb_id))

                except socket.timeout:
                    break

        finally:
            sock.close()

        self.entries = entries

_DISCOVERY = Discovery()

def get_all() -> [Discovered]:
    """Scan network for all iZone controllers."""
    if not _DISCOVERY.entries:
        _DISCOVERY.scan()
    return _DISCOVERY.entries

def get_by_uid(uid) -> Discovered:
    """Scan network and return a controller matching a particular id"""
    entries = get_all()
    for entry in entries:
        if entry['id'] == uid:
            return entry
    return None
    