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

Power support is off by default. Enable at runtime before create/refresh when
the consumer needs iPower (for example future Home Assistant power entities)::

    import pizone
    pizone.ENABLE_POWER = True
"""

import sys
from types import ModuleType
from typing import Any

from . import power as power_mod
from .const import PLACEHOLDER_DEVICE_UID
from .controller import Controller
from .discovery import DiscoveryService, Listener, create_discovery, discovery
from .exceptions import (
    ControllerCommandError,
    ResponseDecodeError,
    UnpairedBridgeError,
)
from .power import BatteryLevel, Power, PowerChannel, PowerDevice, PowerGroup
from .types import ControllerEndpoint
from .zone import Zone


def __getattr__(name: str) -> Any:
    if name == "ENABLE_POWER":
        return power_mod.ENABLE_POWER
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _PizoneModule(ModuleType):
    """Allow ``pizone.ENABLE_POWER = True`` to update :mod:`pizone.power`."""

    @property
    def ENABLE_POWER(self) -> bool:
        return power_mod.ENABLE_POWER

    @ENABLE_POWER.setter
    def ENABLE_POWER(self, value: bool) -> None:
        power_mod.ENABLE_POWER = bool(value)


sys.modules[__name__].__class__ = _PizoneModule

__all__ = [
    "ENABLE_POWER",
    "PLACEHOLDER_DEVICE_UID",
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
    "UnpairedBridgeError",
    "Zone",
    "create_discovery",
    "discovery",
]
