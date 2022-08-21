"""
Power object.

Various bits of data relating to power monitoring.
"""

from __future__ import annotations

import json
import logging
from enum import IntEnum, unique
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_LOG = logging.getLogger("pizone.power")


@unique
class BatteryLevel(IntEnum):
    """Battery level for power device"""

    CRITICAL = 0
    """Reading <600"""

    LOW = 1
    """600-700"""

    NORMAL = 2
    """700-800"""

    FULL = 3
    """>800"""


class PowerChannel:
    """Channel within power device"""

    def __init__(self, device: PowerDevice, index: int):
        self._device = device
        self._index = index

    @property
    def _config(self) -> Dict[str, Any]:
        # pylint: disable=protected-access
        return self._device._config["Channels"][self._index]

    @property
    def _status(self) -> Dict[str, Any]:
        # pylint: disable=protected-access
        return self._device._status["Ch"][self._index]

    @property
    def device(self) -> PowerDevice:
        """Gets the parent power device"""
        return self._device

    @property
    def index(self) -> int:
        """Channel index"""
        return self._index

    @property
    def enabled(self) -> bool:
        """Add to group total."""
        return bool(self._config["Enabled"])

    @property
    def name(self) -> str:
        """Power channel name."""
        return self._config["Name"]

    @property
    def group_number(self) -> Optional[int]:
        """Group number"""
        num = self._config["GrNo"]
        return num if num < 255 else None

    @property
    def generate(self) -> bool:
        """True if this channel is generating power"""
        return bool(self._config["Generate"])

    @property
    def add_to_total(self) -> bool:
        """Add to group total"""
        return bool(self._config["AddToTotal"])

    @property
    def status_power(self) -> int:
        """Power in watts"""
        return self._status["Pwr"]


class PowerDevice:
    """Device for power information."""

    def __init__(self, power: Power, index: int):
        self._power = power
        self._index = index
        self._channels = tuple(PowerChannel(self, i) for i in range(0, 3))

    @property
    def _config(self) -> Dict[str, Any]:
        # pylint: disable=protected-access
        return self._power._config["Devices"][self._index]

    @property
    def _status(self) -> Dict[str, Any]:
        # pylint: disable=protected-access
        return self._power._status["Dev"][self._index]

    @property
    def index(self) -> int:
        """Device index."""
        return self._index

    @property
    def enabled(self) -> bool:
        """Enabled flag"""
        return bool(self._config["Enabled"])

    @property
    def status_ok(self) -> bool:
        """True if device OK"""
        return bool(self._status["Ok"])

    @property
    def status_batt(self) -> BatteryLevel:
        """Battery level"""
        return BatteryLevel(self._status["Batt"])

    @property
    def channels(self) -> Tuple[PowerChannel, ...]:
        """All known devices, a 5-length tuple"""
        return self._channels


class PowerGroup:
    """Grouped power devices"""

    def __init__(self, power: Power, channels: Iterable[PowerChannel]):
        self._power = power
        self._channels = tuple(channels)
        # get unique devices
        devices: Set[PowerDevice] = set()
        for chan in channels:
            devices.add(chan.device)
        self._devices = tuple(devices)

    @property
    def group_number(self) -> int:
        """The group id."""
        return self._channels[0].group_number or -1

    @property
    def name(self) -> str:
        """The group name."""
        return self._channels[0].name

    @property
    def status_ok(self) -> bool:
        """True if the power group is connected"""
        return all(d.status_ok for d in self._devices)

    @property
    def status_power(self) -> int:
        """Currently using power status."""
        return self._channels[0].status_power


class Power:
    """Contains power information."""

    def __init__(self, controller) -> None:
        """Init function."""
        # pylint: disable=import-outside-toplevel, unused-import
        from .controller import Controller

        self._controller = controller  # type: Controller
        self._config = {}  # type: Dict[str, Any]
        self._status = {"LastReadingNo": -1}  # type: Dict[str, Any]
        self._devices = tuple(PowerDevice(self, i) for i in range(0, 5))
        self._groups = None  # type: Optional[Tuple[PowerGroup, ...]]

    async def init(self) -> None:
        """Initialise the power settings."""
        self._config = await self._do_request(1, "PowerMonitorConfig")
        gdict: Dict[int, List[PowerChannel]] = {}
        for dev in self.devices:
            for chan in dev.channels:
                if chan.group_number is not None:
                    gdict.setdefault(chan.group_number, []).append(chan)
        self._groups = tuple(PowerGroup(self, gl) for gl in gdict.values())

    async def refresh(self) -> bool:
        """Refreshes the power usage data."""
        status = await self._do_request(2, "PowerMonitorStatus")  # type: Dict[str, Any]

        if status["LastReadingNo"] == self._status["LastReadingNo"]:
            return False

        self._status = status
        return True

    async def _do_request(self, req_type: int, result: str) -> Dict[str, Any]:
        # pylint: disable=protected-access
        datas = await self._controller._send_command_async(
            "PowerRequest", {"PowerRequest": {"Type": req_type, "No": 0, "No1": 0}}
        )
        data = json.loads(datas)
        return data[result]

    @property
    def enabled(self) -> bool:
        """True if the power settings are enabled."""
        return self._config["Enabled"]

    @property
    def voltage(self) -> int:
        """Power system voltage in V."""
        return self._config["Voltage"]

    @property
    def power_factor(self) -> int:
        """Power factor in %."""
        return self._config["PF"]

    @property
    def cost_of_power(self) -> int:
        """Cost of power in 0.01 cents per pWh."""
        return self._config["CostOfPower"]

    @property
    def emissions(self) -> int:
        """Emissions in gCOe per kWh."""
        return self._config["Emissions"]

    @property
    def status_last_reading(self) -> int:
        """Emissions in gCOe per kWh."""
        return self._status["lastReadingNo"]

    @property
    def devices(self) -> Tuple[PowerDevice, ...]:
        """All known devices, a 5-length tuple"""
        return self._devices

    @property
    def groups(self) -> Optional[Tuple[PowerGroup, ...]]:
        """Available power groups."""
        return self._groups
