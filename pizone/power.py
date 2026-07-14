"""
Power monitor interface.

Properties for reading power monitoring configuration and status data.
"""

from __future__ import annotations

import json
import logging
from enum import IntEnum, unique
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, cast

from .exceptions import ControllerCommandError

if TYPE_CHECKING:
    from .controller import Controller

_LOG = logging.getLogger("pizone.power")


@unique
class BatteryLevel(IntEnum):
    """Battery level for a power device."""

    CRITICAL = 0
    """Reading below 600."""

    LOW = 1
    """Reading between 600 and 700."""

    NORMAL = 2
    """Reading between 700 and 800."""

    FULL = 3
    """Reading above 800."""


class PowerChannel:
    """Channel within a power device."""

    def __init__(self, device: PowerDevice, index: int) -> None:
        self._device = device
        self._index = index

    @property
    def _config(self) -> dict[str, Any]:
        # pylint: disable=protected-access
        return cast(dict[str, Any], self._device._config["Channels"][self._index])

    @property
    def _status(self) -> dict[str, Any]:
        # pylint: disable=protected-access
        return cast(dict[str, Any], self._device._status["Ch"][self._index])

    @property
    def device(self) -> PowerDevice:
        """Parent power device."""
        return self._device

    @property
    def index(self) -> int:
        """Channel index."""
        return self._index

    @property
    def enabled(self) -> bool:
        """Return whether this channel is enabled."""
        return bool(self._config["Enabled"])

    @property
    def name(self) -> str:
        """Power channel name."""
        return cast(str, self._config["Name"])

    @property
    def group_number(self) -> int | None:
        """Group number, or ``None`` if not assigned."""
        num = cast(int, self._config["GrNo"])
        return num if num < 255 else None

    @property
    def generate(self) -> bool:
        """Return whether this channel is generating power."""
        return bool(self._config["Generate"])

    @property
    def add_to_total(self) -> bool:
        """Return whether this channel is included in the group total."""
        return bool(self._config["AddToTotal"])

    @property
    def status_power(self) -> int:
        """Current power reading in watts."""
        return cast(int, self._status["Pwr"])


class PowerDevice:
    """Power monitor device."""

    def __init__(self, power: Power, index: int) -> None:
        self._power = power
        self._index = index
        self._channels = tuple(PowerChannel(self, i) for i in range(3))

    @property
    def _config(self) -> dict[str, Any]:
        # pylint: disable=protected-access
        return cast(dict[str, Any], self._power._config["Devices"][self._index])

    @property
    def _status(self) -> dict[str, Any]:
        # pylint: disable=protected-access
        return cast(dict[str, Any], self._power._status["Dev"][self._index])

    @property
    def index(self) -> int:
        """Device index."""
        return self._index

    @property
    def enabled(self) -> bool:
        """Return whether this device is enabled."""
        return bool(self._config["Enabled"])

    @property
    def status_ok(self) -> bool:
        """Return whether the device status is OK."""
        return bool(self._status["Ok"])

    @property
    def status_batt(self) -> BatteryLevel:
        """Battery level."""
        return BatteryLevel(self._status["Batt"])

    @property
    def channels(self) -> tuple[PowerChannel, ...]:
        """Channels on this device."""
        return self._channels


class PowerGroup:
    """Grouped power channels."""

    def __init__(self, power: Power, channels: Iterable[PowerChannel]) -> None:
        self._power = power
        self._channels = tuple(channels)
        devices: set[PowerDevice] = set()
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
        """Return whether all devices in the group are OK."""
        return all(d.status_ok for d in self._devices)

    @property
    def status_power(self) -> int:
        """Current power usage for the group in watts."""
        return self._channels[0].status_power


class Power:
    """Power monitor data for an iZone controller.

    Owned by a :class:`~pizone.controller.Controller`. Obtain via
    :attr:`~pizone.controller.Controller.power` after the controller has
    initialized; do not construct in application code.

    **Reading state:** properties return cached configuration and status data.
    They do not perform I/O.

    **Updating state:** :meth:`init` and :meth:`refresh` perform HTTP I/O via
    the parent :class:`~pizone.controller.Controller` and raise
    :exc:`ConnectionError` on failure. They may raise
    :exc:`~pizone.exceptions.ControllerCommandError` when the device responds
    but rejects the request.
    """

    def __init__(self, controller: Controller) -> None:
        """Attach this power monitor to *controller*. Used by the controller only."""
        self._controller = controller
        self._config: dict[str, Any] = {}
        self._status: dict[str, Any] = {"LastReadingNo": -1}
        self._devices = tuple(PowerDevice(self, i) for i in range(5))
        self._groups: tuple[PowerGroup, ...] | None = None
        self._power_ok: bool = True

    async def init(self) -> None:
        """Load power monitor configuration from the device.

        Raises:
            ConnectionError: If the HTTP request fails.
            json.JSONDecodeError: If the device response is not valid JSON.
            KeyError: If the response is missing required fields.
        """
        self._config = await self._do_request(1, "PowerMonitorConfig")
        gdict: dict[int, list[PowerChannel]] = {}
        for dev in self.devices:
            for chan in dev.channels:
                if chan.group_number is not None:
                    gdict.setdefault(chan.group_number, []).append(chan)
        self._groups = tuple(PowerGroup(self, gl) for gl in gdict.values())

    async def refresh(self) -> bool:
        """Refresh power usage data from the device.

        Returns:
            ``True`` if the cached status changed, otherwise ``False``.

        Raises:
            ConnectionError: If the HTTP request fails.
            json.JSONDecodeError: If the device response is not valid JSON.
            KeyError: If the response is missing required fields.
        """
        status: dict[str, Any] = await self._do_request(2, "PowerMonitorStatus")

        if status["LastReadingNo"] == self._status["LastReadingNo"]:
            return False

        self._status = status
        return True

    async def _do_request(self, req_type: int, result: str) -> dict[str, Any]:
        """Send a power monitor request to the device.

        Raises:
            ConnectionError: If the HTTP request fails.
            json.JSONDecodeError: If the device response is not valid JSON.
            KeyError: If the response is missing *result*.
        """
        if not self._controller.bridge_connected:
            self._set_power_ok(False)
            raise ConnectionError("Bridge not connected")
        try:
            # pylint: disable=protected-access
            datas = await self._controller._http_post(
                "PowerRequest",
                {"PowerRequest": {"Type": req_type, "No": 0, "No1": 0}},
            )
            data = json.loads(datas)
            payload = cast(dict[str, Any], data[result])
        except (
            ConnectionError,
            ControllerCommandError,
            json.JSONDecodeError,
            KeyError,
        ) as ex:
            self._set_power_ok(False)
            if isinstance(ex, ConnectionError):
                raise
            if isinstance(ex, json.JSONDecodeError):
                raise ConnectionError("Invalid power monitor response") from ex
            if isinstance(ex, KeyError):
                raise ConnectionError("Invalid power monitor response") from ex
            raise ConnectionError("Power monitor request failed") from ex
        self._set_power_ok(True)
        return payload

    def _set_power_ok(self, ok: bool) -> None:
        if self._power_ok == ok:
            return
        self._power_ok = ok
        # pylint: disable=protected-access
        self._controller._event_coordinator.power_update(self._controller)

    @property
    def connected(self) -> bool:
        """True while the bridge is up and power monitor I/O is healthy."""
        return self._controller.bridge_connected and self._power_ok

    @property
    def enabled(self) -> bool:
        """True if the power settings are enabled."""
        return bool(self._config["Enabled"])

    @property
    def voltage(self) -> int:
        """Power system voltage in V."""
        return cast(int, self._config["Voltage"])

    @property
    def power_factor(self) -> int:
        """Power factor in %."""
        return cast(int, self._config["PF"])

    @property
    def cost_of_power(self) -> int:
        """Cost of power in 0.01 cents per pWh."""
        return cast(int, self._config["CostOfPower"])

    @property
    def emissions(self) -> int:
        """Emissions in gCOe per kWh."""
        return cast(int, self._config["Emissions"])

    @property
    def status_last_reading(self) -> int:
        """Last reading number from the device."""
        return cast(int, self._status["LastReadingNo"])

    @property
    def devices(self) -> tuple[PowerDevice, ...]:
        """All known power monitor devices."""
        return self._devices

    @property
    def groups(self) -> tuple[PowerGroup, ...] | None:
        """Available power groups."""
        return self._groups
