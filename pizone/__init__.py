"""Interface to the iZone air conditioner controller.

Prefer :func:`~pizone.discovery.create_discovery` or
:func:`~pizone.discovery.discovery` to obtain a discovery service, then create
or discover :class:`~pizone.controller.Controller` instances through that
service. Zones and power monitors are owned by their controller.

Synchronous property reads return cached device data and do not raise
:exc:`ConnectionError`. Async command and refresh methods perform HTTP I/O and
raise :exc:`ConnectionError` when the device cannot be reached. They raise
:exc:`~pizone.exceptions.ControllerCommandError` when the device responds but
rejects the request (``{ERROR...}`` body or HTTP 4xx).

Layered connection state on :class:`~pizone.controller.Controller`:

- :attr:`~pizone.controller.Controller.bridge_connected` — ASH bridge HTTP transport
- :attr:`~pizone.controller.Controller.connected` — bridge plus valid iZone AC data
- :attr:`~pizone.power.Power.connected` — power monitor I/O (when enabled)
"""

from .controller import Controller
from .discovery import DiscoveryService, Listener, create_discovery, discovery
from .exceptions import ControllerCommandError, ResponseDecodeError
from .power import BatteryLevel, Power, PowerChannel, PowerDevice, PowerGroup
from .types import ControllerEndpoint
from .zone import Zone

__all__ = [
    "BatteryLevel",
    "Controller",
    "ControllerCommandError",
    "ControllerEndpoint",
    "DiscoveryService",
    "Listener",
    "Power",
    "PowerChannel",
    "PowerDevice",
    "PowerGroup",
    "ResponseDecodeError",
    "Zone",
    "create_discovery",
    "discovery",
]
