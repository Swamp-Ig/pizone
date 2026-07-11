"""
Zone interface.

Properties for reading and setting zone data.
"""

from enum import Enum
from typing import Any, Dict, Union


class Zone:
    """Interface to an iZone zone.

    **Reading state:** properties return cached zone data and do not perform
    I/O. They do not raise :exc:`ConnectionError` when the parent controller
    is disconnected.

    **Updating state:** async command methods perform HTTP I/O via the parent
    :class:`~pizone.controller.Controller` and raise :exc:`ConnectionError` on
    failure.
    """

    class Type(Enum):
        """Zone type enumeration.

        Possible values are:

        ``auto`` – the zone has temperature control enabled
        ``opcl`` – the zone is open/close only
        ``const`` – the zone is a constant zone
        """

        AUTO = "auto"
        OPCL = "opcl"
        CONST = "const"

    class Mode(Enum):
        """Current zone mode.

        Possible values are:

        ``open`` – the zone is currently open
        ``close`` – the zone is currently closed
        ``auto`` – the zone is currently in temperature control mode
        """

        OPEN = "open"
        CLOSE = "close"
        AUTO = "auto"

    DictValue = Union[str, int, float]
    ZoneData = Dict[str, DictValue]

    def __init__(self, controller: Any, index: int) -> None:
        self._zone_data: Zone.ZoneData = {}
        self._index = index
        self._controller = controller

    @property
    def index(self) -> int:
        """The index of the zone."""
        return self._index

    @property
    def name(self) -> str:
        """Zone name."""
        return self._get_zone_state("Name")

    @property
    def type(self) -> "Zone.Type":
        """Zone type.

        Raises:
            ValueError: If the cached value is not a valid :class:`Type` member.
        """
        return self.Type(self._get_zone_state("Type"))

    @property
    def mode(self) -> "Zone.Mode":
        """Current zone mode.

        Raises:
            ValueError: If the cached value is not a valid :class:`Mode` member.
        """
        return self.Mode(self._get_zone_state("Mode"))

    @property
    def temp_setpoint(self) -> float | None:
        """Temperature setpoint in degrees C."""
        return self._get_zone_state("SetPoint") or None

    @property
    def temp_current(self) -> float | None:
        """Current zone temperature."""
        return self._get_zone_state("Temp") or None

    @property
    def airflow_max(self) -> int:
        """Maximum allowed airflow for the zone as a percent."""
        return self._get_zone_state("MaxAir")

    @property
    def airflow_min(self) -> int:
        """Minimum allowed airflow for the zone as a percent."""
        return self._get_zone_state("MinAir")

    async def set_airflow_min(self, value: int) -> None:
        """Change the zone minimum airflow in 5% increments.

        Raises:
            AttributeError: If the value is out of range or not divisible by 5.
            ConnectionError: If the device cannot be reached or the response is invalid.
        """
        if value % 5 != 0:
            raise AttributeError(f"MinAir '{value}' not rounded to nearest 5")
        if value < 0 or value > 100:
            raise AttributeError(f"MinAir '{value}' is out of range")

        await self._send_command("AirMinCommand", value)
        self._zone_data["MinAir"] = value
        self._fire_listeners()

    async def set_airflow_max(self, value: int) -> None:
        """Change the zone maximum airflow in 5% increments.

        Raises:
            AttributeError: If the value is out of range or not divisible by 5.
            ConnectionError: If the device cannot be reached or the response is invalid.
        """
        if value % 5 != 0:
            raise AttributeError(f"MaxAir '{value}' not rounded to nearest 5")
        if value < 0 or value > 100:
            raise AttributeError(f"MaxAir '{value}' is out of range")

        await self._send_command("AirMaxCommand", value)
        self._zone_data["MaxAir"] = value
        self._fire_listeners()

    async def set_temp_setpoint(self, value: float) -> None:
        """Change the zone temperature setpoint in degrees C.

        Valid values are between the controller minimum and maximum temperature
        in half-degree increments.

        Raises:
            AttributeError: If the zone is not temperature controlled or the
                value is out of range.
            ConnectionError: If the device cannot be reached or the response is invalid.
        """
        if self.type != Zone.Type.AUTO:
            raise AttributeError(f"Can't set SetPoint to '{self.type}' type zone.")
        if value % 0.5 != 0:
            raise AttributeError(f"SetPoint '{value}' not rounded to nearest 0.5")
        if value < self._controller.temp_min or value > self._controller.temp_max:
            raise AttributeError(f"SetPoint '{value}' is out of range")

        await self._send_command("ZoneCommand", value)
        self._zone_data["Mode"] = "auto"
        self._zone_data["SetPoint"] = value
        self._fire_listeners()

    async def set_mode(self, value: "Zone.Mode") -> None:
        """Set the current zone mode.

        Raises:
            AttributeError: If auto mode is requested on an open/close zone.
            ConnectionError: If the device cannot be reached or the response is invalid.
        """
        if value == Zone.Mode.AUTO:
            if self.type != Zone.Type.AUTO:
                raise AttributeError("Can't use auto mode on open/close zone.")
            await self._send_command("ZoneCommand", self._get_zone_state("SetPoint"))
            self._zone_data["Mode"] = "auto"
        else:
            await self._send_command("ZoneCommand", value.value)
            self._zone_data["Mode"] = value.value
        self._fire_listeners()

    def _update_zone(self, zone_data: "Zone.ZoneData", notify: bool = True) -> None:
        """Replace cached zone data from a device refresh.

        Raises:
            AttributeError: If the response index does not match this zone.
        """
        if zone_data["Index"] != self._index:
            raise AttributeError("Can't change index of existing zone.")
        self._zone_data = zone_data
        if notify:
            self._fire_listeners()

    def _fire_listeners(self) -> None:
        # pylint: disable=protected-access
        self._controller._event_coordinator.zone_update(self._controller, self)

    def _get_zone_state(self, state: str) -> Any:
        return self._zone_data.get(state)

    async def _send_command(self, command: str, data: Union[str, float, int]) -> None:
        """Send a zone command via the parent controller.

        Raises:
            ConnectionError: If the device cannot be reached or the response is invalid.
        """
        send_data = {command: {"ZoneNo": str(self._index + 1), "Command": str(data)}}
        # pylint: disable=protected-access
        await self._controller._send_command_async(command, send_data)
