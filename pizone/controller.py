"""Controller module"""

from __future__ import annotations

import asyncio
import json
import logging
from asyncio import Condition, Lock
from enum import Enum
from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any, Dict, Union, cast

import aiohttp

from .power import Power
from .zone import Zone

if TYPE_CHECKING:
    from .discovery import DiscoveryService, Listener

_LOG = logging.getLogger("pizone.controller")


class Controller:
    """Interface to IZone controller"""

    class Mode(Enum):
        """Valid controller modes"""

        COOL = "cool"
        HEAT = "heat"
        VENT = "vent"
        DRY = "dry"
        AUTO = "auto"
        FREE_AIR = "free_air"

    class Fan(Enum):
        """All fan modes"""

        LOW = "low"
        MED = "med"
        HIGH = "high"
        TOP = "top"
        AUTO = "auto"

    DictValue = Union[str, int, float]
    ControllerData = Dict[str, DictValue]

    REQUEST_TIMEOUT = 3
    """Time to wait for results from server."""

    REFRESH_INTERVAL = 25.0
    """Interval between refreshes of data."""

    UPDATE_REFRESH_DELAY = 5.0
    """Delay after updating data before a refresh."""

    _VALID_FAN_MODES = {
        "disabled": [Fan.LOW, Fan.MED, Fan.HIGH],
        "unknown": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.TOP, Fan.AUTO],
        "4-speed": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.TOP, Fan.AUTO],
        "3-speed": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO],
        "2-speed": [Fan.LOW, Fan.HIGH, Fan.AUTO],
        "var-speed": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO],
    }

    def __init__(
        self,
        discovery_service: DiscoveryService,
        event_coordinator: Listener,
        *,
        device_uid: str,
        device_ip: str,
        is_v2: bool,
        is_ipower: bool,
    ) -> None:
        """Create a controller interface.

        Usually this is called from the discovery service. If neither
        device UID or address are specified, will search network for
        exactly one controller. If UID is specified then the addr is
        ignored.

        Args:
            device_uid: Controller UId as a string (eg: mine is '000013170')
                If specified, will search the network for a matching device
            device_addr: Device network address. Usually specified as IP
                address

        Property reads return cached data and do not raise when disconnected;
        check :attr:`connected` or handle :exc:`ConnectionError` from async
        methods instead.
        """
        self._ip = device_ip
        self._discovery_service = discovery_service
        self._event_coordinator = event_coordinator
        self._device_uid = device_uid
        self._is_v2 = is_v2
        self._is_ipower = is_ipower

        self.zones: list[Zone] = []
        self.fan_modes: list[Controller.Fan] = []
        self._system_settings: Controller.ControllerData = {}
        self._power: Power | None = None

        self._initialized: bool = False
        self._fail_exception: Exception | None = None

        self._sending_lock = Lock()
        self._scan_condition = Condition()

    async def _initialize(self) -> None:
        """Initialize the controller, does not complete until the system is
        initialized."""
        await self._refresh_system(notify=False)

        self.fan_modes = Controller._VALID_FAN_MODES[
            str(self._system_settings.get("FanAuto", "disabled"))
        ]

        zone_count = int(self._system_settings["NoOfZones"])
        self.zones = [Zone(self, i) for i in range(zone_count)]
        await self._refresh_zones(notify=False)

        if self._is_ipower:
            self._power = Power(self)
            await self._power.init()
            if self._power.enabled:
                await self._refresh_power(notify=False)
        else:
            self._power = None

        self._initialized = True
        self._discovery_service.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            try:
                async with asyncio.timeout(Controller.REFRESH_INTERVAL):
                    async with self._scan_condition:
                        await self._scan_condition.wait()
                # triggered rescan, short delay
                await asyncio.sleep(Controller.UPDATE_REFRESH_DELAY)
            except asyncio.TimeoutError:
                pass

            if self._discovery_service.is_closed:
                return

            # pylint: disable=broad-except
            try:
                _LOG.debug("Polling unit %s.", self._device_uid)
                await self._refresh_all()
            except ConnectionError:
                _LOG.debug("Poll failed due to exception.", exc_info=True)
            except Exception:
                _LOG.error("Unexpected exception", exc_info=True)

    async def refresh(self) -> None:
        """Queue a refresh of all controller data."""
        async with self._scan_condition:
            self._scan_condition.notify()

    @property
    def connected(self) -> bool:
        """True while the controller can reach the device over HTTP."""
        return self._fail_exception is None

    @property
    def power(self) -> Power | None:
        """Power info"""
        return self._power

    @property
    def device_ip(self) -> str:
        """IP Address of the unit"""
        return self._ip

    @property
    def device_uid(self) -> str:
        """UId of the unit"""
        return self._device_uid

    @property
    def is_v2(self) -> bool:
        """True if this is a v2 controller"""
        return self._is_v2

    @property
    def discovery(self) -> Any:
        """The discovery service"""
        return self._discovery_service

    @property
    def is_on(self) -> bool:
        """True if the system is turned on"""
        return self._get_system_state("SysOn") == "on"

    async def set_on(self, value: bool) -> None:
        """Turn the system on or off."""
        await self._set_system_state("SysOn", "SystemON", "on" if value else "off")

    @property
    def mode(self) -> "Mode":
        """System mode, cooling, heating, etc"""
        if self.free_air:
            return self.Mode.FREE_AIR
        return self.Mode(self._get_system_state("SysMode"))

    async def set_mode(self, value: Mode) -> None:
        """Set system mode, cooling, heating, etc."""
        if value == Controller.Mode.FREE_AIR:
            if self.free_air:
                return
            if not self.free_air_enabled:
                raise AttributeError("Free air system is not enabled")
            await self._set_system_state("FreeAir", "FreeAir", "on")
        else:
            if self.free_air:
                await self._set_system_state("FreeAir", "FreeAir", "off")
            await self._set_system_state("SysMode", "SystemMODE", value.value)

    @property
    def fan(self) -> "Fan":
        """The current fan level."""
        return self.Fan(self._get_system_state("SysFan"))

    async def set_fan(self, value: Fan) -> None:
        """The fan level. Not all fan modes are allowed depending on the system.
        Raises:
            AttributeError: On setting if the argument value is not valid
        """
        if value not in self.fan_modes:
            raise AttributeError(f"Fan mode {value.value} not allowed")
        await self._set_system_state(
            "SysFan",
            "SystemFAN",
            value.value,
            "medium" if value is Controller.Fan.MED else value.value,
        )

    @property
    def sleep_timer(self) -> int:
        """Current setting for the sleep timer."""
        return int(self._get_system_state("SleepTimer"))

    async def set_sleep_timer(self, value: int) -> None:
        """The sleep timer.
        Valid settings are 0, 30, 60, 90, 120
        Raises:
            AttributeError: On setting if the argument value is not valid
        """
        time = int(value)
        if time < 0 or time > 120 or time % 30 != 0:
            raise AttributeError(
                f'Invalid Sleep Timer "{value}", must be divisible by 30'
            )
        await self._set_system_state("SleepTimer", "SleepTimer", value, time)

    @property
    def free_air_enabled(self) -> bool:
        """Test if the system has free air system available"""
        return self._get_system_state("FreeAir") != "disabled"

    @property
    def free_air(self) -> bool:
        """True if the free air system is turned on. False if unavailable or off"""
        return self._get_system_state("FreeAir") == "on"

    async def set_free_air(self, value: bool) -> None:
        """Turn the free air system on or off.
        Raises:
            AttributeError: If attempting to set the state of the free air
                system when it is not enabled.
        """
        if not self.free_air_enabled:
            raise AttributeError("Free air is disabled")
        await self._set_system_state("FreeAir", "FreeAir", "on" if value else "off")

    @property
    def temp_supply(self) -> float | None:
        """Current supply, or in duct, air temperature."""
        return float(self._get_system_state("Supply")) or None

    @property
    def temp_setpoint(self) -> float | None:
        """AC unit setpoint temperature.
        This is the unit target temp with with rasMode == RAS,
        or with rasMode == master and ctrlZone == 13.
        """
        return float(self._get_system_state("Setpoint")) or None

    async def set_temp_setpoint(self, value: float) -> None:
        """AC unit setpoint temperature.
        This is the unit target temp with with rasMode == RAS,
        or with rasMode == master and ctrlZone == 13.
        Args:
            value: Valid settings are between ecoMin and ecoMax, at
            0.5 degree units.
        Raises:
            AttributeError: On setting if the argument value is not valid.
                Can still be set even if the mode isn't appropriate.
        """
        if value % 0.5 != 0:
            raise AttributeError(f"SetPoint '{value}' not rounded to nearest 0.5")
        if value < self.temp_min or value > self.temp_max:
            raise AttributeError(f"SetPoint '{value}' is out of range")
        await self._set_system_state("Setpoint", "UnitSetpoint", value, str(value))

    @property
    def temp_return(self) -> float | None:
        """The return, or room, air temperature"""
        return float(self._get_system_state("Temp")) or None

    @property
    def eco_lock(self) -> bool:
        """True if eco lock setting is on."""
        return self._get_system_state("EcoLock") == "true"

    @property
    def temp_min(self) -> float:
        """The value for the eco lock minimum, or 15 if eco lock not set"""
        return float(self._get_system_state("EcoMin")) if self.eco_lock else 15.0

    @property
    def temp_max(self) -> float:
        """The value for the eco lock maximum, or 30 if eco lock not set"""
        return float(self._get_system_state("EcoMax")) if self.eco_lock else 30.0

    @property
    def ras_mode(self) -> str:
        """This indicates the current selection of the Return Air temperature Sensor.
        Possible values are:
            master: the AC unit is controlled from a CTS, which is manually
                selected.
            RAS:    the AC unit is controller from its own return air sensor
            zones:  the AC unit is controlled from a CTS, which is
                automatically selected dependant on the cooling/ heating need
                of zones.
        """
        return self._get_system_state("RAS")

    @property
    def zone_ctrl(self) -> int:
        """This indicates the zone that currently controls the AC unit.
        Value interpreted in combination with rasMode"""
        return int(self._get_system_state("CtrlZone"))

    @property
    def zones_total(self) -> int:
        """This indicates the number of zones the system is configured for."""
        return int(self._get_system_state("NoOfZones"))

    @property
    def zones_const(self) -> int:
        """This indicates the number of constant zones the system is
        configured for."""
        return self._get_system_state("NoOfConst")

    @property
    def sys_type(self) -> str:
        """This indicates the type of the iZone system connected. Possible values are:
        110: the system is zone control only and all the zones
            are OPEN/CLOSE zones
        210: the system is zone control only. Zones can be temperature
            controlled, dependant on the zone settings.
        310: the system is zone control and unit control.
        """
        return self._get_system_state("SysType")

    async def _refresh_all(self, notify: bool = True) -> None:
        zones = int(self._system_settings["NoOfZones"])
        # this has to be done sequentially
        await self._refresh_system(notify)
        await self._refresh_power(notify)
        for i in range(0, zones, 4):
            await self._refresh_zone_group(i, notify)

    async def _refresh_system(self, notify: bool = True) -> None:
        """Refresh the system settings."""
        values: Controller.ControllerData = await self._get_resource("SystemSettings")
        if self._device_uid != values["AirStreamDeviceUId"]:
            _LOG.error("_refresh_system called with non-matching device ID")
            return

        self._system_settings = values

        if notify:
            self._event_coordinator.controller_update(self)

    async def _refresh_power(self, notify: bool = True) -> None:
        if self._power is None or not self._power.enabled:
            return

        updated = await self._power.refresh()

        if updated and notify:
            self._event_coordinator.power_update(self)

    async def _refresh_zones(self, notify: bool = True) -> None:
        """Refresh the Zone information."""
        zones = int(self._system_settings["NoOfZones"])
        await asyncio.gather(
            *[self._refresh_zone_group(i, notify) for i in range(0, zones, 4)]
        )

    async def _refresh_zone_group(self, group: int, notify: bool = True) -> None:
        assert group in [0, 4, 8]
        zone_data_part = await self._get_resource(f"Zones{group + 1}_{group + 4}")

        for i in range(min(len(self.zones) - group, 4)):
            zone_data = zone_data_part[i]
            # pylint: disable=protected-access
            self.zones[i + group]._update_zone(zone_data, notify)

    def _refresh_address(self, address: str) -> None:
        """Called from discovery to update the address"""
        self._ip = address
        # Signal to the retry connection loop to have another go.
        if self._fail_exception:
            self._discovery_service.create_task(self._retry_connection())

    def _get_system_state(self, state: str) -> Any:
        return self._system_settings.get(state)

    async def _set_system_state(
        self,
        state: str,
        command: str,
        value: DictValue,
        send: Any | None = None,
    ) -> None:
        if send is None:
            send = value
        await self._send_command_async(command, {command: send})

        # Update state and trigger rescan
        self._system_settings[state] = value
        self._event_coordinator.controller_update(self)
        await self.refresh()

    def _failed_connection(self, ex: Exception) -> None:
        if self._fail_exception:
            self._fail_exception = ex
            return
        self._fail_exception = ex
        if not self._initialized:
            return
        self._event_coordinator.controller_disconnected(self, ex)

    def _restored_connection(self) -> None:
        """Clear a prior connection failure after successful I/O."""
        if self._fail_exception is None:
            return
        self._fail_exception = None
        if not self._initialized:
            return
        self._event_coordinator.controller_update(self)
        for zone in self.zones:
            self._event_coordinator.zone_update(self, zone)
        self._event_coordinator.power_update(self)
        self._event_coordinator.controller_reconnected(self)

    async def _retry_connection(self) -> None:
        _LOG.info(
            "Attempting to reconnect to server uid=%s ip=%s",
            self.device_uid,
            self.device_ip,
        )

        try:
            await self._refresh_all(notify=False)
        except ConnectionError:
            # Expected, just carry on.
            _LOG.warning(
                "Reconnect attempt for uid=%s failed with exception",
                self.device_uid,
                exc_info=True,
            )

    async def _get_resource(self, resource: str) -> Any:
        try:
            session = self._discovery_service.session
            async with (
                self._sending_lock,
                session.get(
                    f"http://{self.device_ip}/{resource}",
                    timeout=Controller.REQUEST_TIMEOUT,
                ) as response,
            ):
                try:
                    result = await response.json(content_type=None)
                except JSONDecodeError as ex:
                    text = await response.text()
                    if text[-4:] == "{OK}":
                        result = json.loads(text[:-4])
                    else:
                        _LOG.error('Decode error for "%s"', text, exc_info=True)
                        raise ConnectionError(
                            "Unable to decode response from the controller"
                        ) from ex
                self._restored_connection()
                return result
        except (asyncio.TimeoutError, aiohttp.ClientError) as ex:
            self._failed_connection(ex)
            raise ConnectionError("Unable to connect to the controller") from ex

    async def _send_command_async(self, command: str, data: Any) -> str:
        # For some reason aiohttp fragments post requests, which causes
        # the server to fail disgracefully. Implemented rough and dirty
        # HTTP POST client.
        loop = asyncio.get_running_loop()
        on_complete = loop.create_future()
        device_ip = self.device_ip

        class _PostProtocol(asyncio.Protocol):
            def __init__(self) -> None:
                self.response = bytearray()

            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                body = json.dumps(data).encode("latin_1")
                header = (
                    f"POST /{command} HTTP/1.1\r\n"
                    f"Host: {device_ip}\r\n"
                    f"Content-Length: {str(len(body))}\r\n"
                    "\r\n"
                ).encode()
                _LOG.debug("Writing message to %s", device_ip)
                cast(asyncio.WriteTransport, transport).write(header + body)

            def data_received(self, data: bytes) -> None:
                self.response += data

            def eof_received(self) -> None:
                full = self.response.decode("latin_1")
                if not full:
                    on_complete.set_exception(RuntimeError("Empty HTTP Response"))
                    return
                header, _ = full.split("\r\n", 1)
                parts = header.split(" ", 2)
                if len(parts) < 3 or parts[0] != "HTTP/1.1":
                    on_complete.set_exception(RuntimeError("Invalid HTTP Response"))
                    return
                if parts[1] != "200":
                    on_complete.set_exception(
                        aiohttp.ClientError(
                            f"Unable to connect to: http://{device_ip}/{command}"
                            f" response={parts[1]} message={parts[2]}"
                        )
                    )
                    return
                _, content = full.split("\r\n\r\n", 1)
                on_complete.set_result(content)

        # The server doesn't tolerate multiple requests in fly concurrently
        try:
            async with self._sending_lock, asyncio.timeout(Controller.REQUEST_TIMEOUT):
                await loop.create_connection(_PostProtocol, self.device_ip, 80)
                await on_complete

            result = on_complete.result()
        except (OSError, asyncio.TimeoutError, aiohttp.ClientError) as ex:
            self._failed_connection(ex)
            raise ConnectionError("Unable to connect to controller") from ex

        if len(result) >= 7 and result[:6] == "{ERROR":
            self._restored_connection()
            raise ConnectionError(f"Server returned error state {result}")
        if len(result) >= 4 and result[-4:] == "{OK}":
            result = result[:-4]
        self._restored_connection()
        return result
