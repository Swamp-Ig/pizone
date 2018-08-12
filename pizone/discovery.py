"""iZone device discovery."""
import socket

from datetime import timedelta
from urllib.parse import unquote

DISCOVERY_MSG = b'IASD'

UDP_PORT = 12107

DISCOVERY_ADDRESS = '<broadcast>'
DISCOVERY_TIMEOUT = timedelta(seconds=2)


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

                    entries.append({
                        'id': cb_id,
                        'ip': address,
                    })

                except socket.timeout:
                    break

        finally:
            sock.close()

        self.entries = entries

_discovery = Discovery()

def get_all():
    if len(_discovery.entries) == 0:
        _discovery.scan()
    return _discovery.entries

def get_ID(id):
    entries = get_all()
    for entry in entries:
        if entry['id'] == id:
            return entry
    return None


def main():
    """Test iZone discovery."""
    from pprint import pprint
    iZone = Discovery()
    pprint("Scanning for iZone devices..")
    iZone.update()
    pprint(iZone.entries)

if __name__ == "__main__":
   main()
    