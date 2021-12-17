"""
Power object.

Various bits of data relating to power monitoring.
"""

import json
import logging
from typing import Dict, Any, Optional, Tuple
from enum import IntEnum, unique

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

    def __init__(self, device, channel):
        self._device = device
        self._channel = channel

    @property
    def _config(self) -> Dict[str, Any]:
        # pylint: disable=protected-access
        return self._device._config["Channels"][self._channel]

    @property
    def _status(self) -> Dict[str, Any]:
        # pylint: disable=protected-access
        return self._device._status["Ch"][self._channel]

    @property
    def channel(self) -> int:
        """Channel index"""
        return self._channel

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
        """Power channel name."""
        num = self._config["GrNo"]
        return num if num < 255 else None

    @property
    def generate(self) -> bool:
        """True if this channel is used in consumpton."""
        return bool(self._config["Generate"])

    @property
    def add_to_total(self) -> bool:
        """Add to group total."""
        return bool(self._config["AddToTotal"])

    @property
    def status_power(self) -> int:
        """Device index."""
        return self._status["Pwr"]


class PowerDevice:
    """Device for power information."""

    def __init__(self, power, index):
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
        """Add to group total."""
        return bool(self._config["Enabled"])

    @property
    def status_ok(self) -> bool:
        """Add to group total."""
        return bool(self._status["Ok"])

    @property
    def status_batt(self) -> BatteryLevel:
        """Add to group total."""
        return BatteryLevel(self._status["Batt"])

    @property
    def channels(self) -> Tuple[PowerChannel, ...]:
        """All known devices, a 5-length tuple"""
        return self._channels


class PowerGroup:
    """Grouped power devices"""

    def __init__(self, power, channel):
        self._power = power
        self._channel = channel

    @property
    def name(self) -> str:
        """The group name."""
        return self._channel.name

    @property
    def status_power(self) -> int:
        """Currently using power status."""
        return self._channel.status_power


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
        groups = {}
        for dev in self.devices:
            for chan in dev.channels:
                grp = chan.group_number
                if grp is not None and grp not in groups:
                    groups[grp] = PowerGroup(self, chan)
        self._groups = tuple(groups.values())

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
            "PowerRequest", {"Type": req_type, "No": 0, "No1": 0}
        )

        try:
            data = json.loads(datas)
        except json.decoder.JSONDecodeError as ex:
            if datas[-4:] == "{OK}":
                data = json.loads(datas[:-4])
            else:
                _LOG.error('Decode error for "%s"', datas, exc_info=True)
                raise ConnectionError(
                    "Unable to decode response, see error log."
                ) from ex
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
    def groups(self):
        """Available power groups."""
        return self._groups
