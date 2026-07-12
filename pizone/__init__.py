"""Interface to the iZone air conditioner controller.

Interaction is mostly through the :class:`~pizone.controller.Controller` and
:class:`~pizone.zone.Zone` classes.

Synchronous property reads return cached device data and do not raise
:exc:`ConnectionError`. Async command and refresh methods perform HTTP I/O and
raise :exc:`ConnectionError` when the device cannot be reached. They raise
:exc:`~pizone.exceptions.ControllerCommandError` when the device responds but
rejects the request (``{ERROR...}`` body or HTTP 4xx).
"""

from .controller import Controller
from .discovery import DiscoveryService, Listener, discovery
from .exceptions import ControllerCommandError
from .power import BatteryLevel, Power, PowerChannel, PowerDevice, PowerGroup
from .zone import Zone

__all__ = [
    "Controller",
    "ControllerCommandError",
    "Zone",
    "DiscoveryService",
    "Listener",
    "discovery",
    "Power",
    "PowerGroup",
    "PowerDevice",
    "PowerChannel",
    "BatteryLevel",
]
