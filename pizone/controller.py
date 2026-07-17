"""iZone controller interface."""

import asyncio
from asyncio import Condition, Lock
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager, nullcontext
from enum import Enum
from functools import wraps
import json
import logging
from typing import TYPE_CHECKING, Any, Self, cast

import aiohttp

from . import power as power_mod
from .exceptions import ControllerCommandError, ResponseDecodeError
from .power import Power
from .types import ControllerEndpoint
from .zone import Zone

if TYPE_CHECKING:
    from .discovery import DiscoveryService, Listener

_LOG = logging.getLogger("pizone.controller")


def _refresh_api(
    method: Callable[..., Awaitable[Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Enter ``Controller._refresh_scope`` around a public ``refresh_*`` method."""

    @wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._refresh_scope():
            return await method(self, *args, **kwargs)

    return wrapper


class Controller:
    """Interface to an iZone controller.

    Obtain instances via :meth:`pizone.discovery.DiscoveryService.create_controller`
    (1.4) or the legacy discovery listener / :meth:`~pizone.discovery.DiscoveryService.fetch_controller`
    path. Do not construct controllers directly in application code.

    **Reading state:** properties and other synchronous accessors return the
    last cached values from the device. They do not perform I/O and do not
    raise :exc:`ConnectionError` when :attr:`connected` is ``False``. Check
    :attr:`connected` before trusting stale data.

    **Updating state:** async command and refresh methods perform HTTP I/O.
    They raise :exc:`ConnectionError` when the device cannot be reached.
    They raise :exc:`~pizone.exceptions.ControllerCommandError` when the
    device responds but rejects the request (``{ERROR...}`` body or HTTP 4xx).

    :attr:`connected` is ``True`` when the ASH bridge is reachable over HTTP
    and the iZone AC subsystem last returned valid system data. Check
    :attr:`bridge_connected` for bridge transport health alone.
    """

    class Mode(Enum):
        """Valid controller modes."""

        COOL = "cool"
        HEAT = "heat"
        VENT = "vent"
        DRY = "dry"
        AUTO = "auto"
        FREE_AIR = "free_air"

    class Fan(Enum):
        """Valid fan modes."""

        LOW = "low"
        MED = "med"
        HIGH = "high"
        TOP = "top"
        AUTO = "auto"

    DictValue = str | int | float
    ControllerData = dict[str, DictValue]

    REQUEST_TIMEOUT = 10
    """Time to wait for a response from the device, in seconds."""

    REFRESH_INTERVAL = 25.0
    """Interval between data refreshes, in seconds."""

    UPDATE_REFRESH_DELAY = 5.0
    """Delay after sending a command before refreshing data, in seconds."""

    _VALID_FAN_MODES = {
        "disabled": [Fan.LOW, Fan.MED, Fan.HIGH],
        "unknown": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.TOP, Fan.AUTO],
        "4-speed": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.TOP, Fan.AUTO],
        "3-speed": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO],
        "2-speed": [Fan.LOW, Fan.HIGH, Fan.AUTO],
        "var-speed": [Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO],
    }

    # Device reports these on fault-shaped SystemSettings (bridge up, AC down).
    # Live samples: tests.resources.FAULT_SYSTEM_SETTINGS_BRIDGE_NO_AC (unpaired)
    # and FAULT_SYSTEM_SETTINGS_PAIRED_COLD_RESTART (paired UID, cold restart).
    _FAULT_STATE_VALUES = frozenset({"error"})
    # Safe property fallbacks when cache is empty or a field is in _FAULT_STATE_VALUES
    _SYSTEM_STATE_DEFAULTS: ControllerData = {
        "SysOn": "off",
        "SysMode": "cool",
        "SysFan": "auto",
        "SleepTimer": 0,
        "FreeAir": "disabled",
        "Supply": "0.0",
        "Setpoint": "0.0",
        "Temp": "0.0",
        "EcoLock": "false",
        "EcoMin": "15.0",
        "EcoMax": "30.0",
        "RAS": "zones",
        "CtrlZone": 0,
        "NoOfZones": 0,
        "NoOfConst": 0,
        "SysType": "0",
        "FanAuto": "disabled",
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
        """Set up controller fields.

        Prefer :meth:`create` or :meth:`from_discovery`. Application code should
        not call this directly.
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
        self._closed: bool = False
        self._bridge_ok: bool = True
        self._izone_ok: bool = True
        self._on_address_changed: Callable[[ControllerEndpoint], None] | None = None

        self._sending_lock = Lock()
        self._scan_condition = Condition()
        self._refresh_depth: int = 0
        self._refresh_fail_ex: ConnectionError | None = None

    @property
    def _legacy_pathway(self) -> bool:
        """True when this controller was created on the legacy discovery path."""
        return self._discovery_service._legacy_pathway  # noqa: SLF001

    @asynccontextmanager
    async def _refresh_scope(self) -> AsyncIterator[None]:
        """Nesting scope for refresh HTTP I/O (1.4 path).

        On the outermost exit: settle ``bridge_ok`` once, and on transport
        failure nudge discovery ``scan()`` (with cooldown). Legacy pathway is
        a no-op — bridge settle stays on ``_get_resource`` / command wrappers.
        """
        if self._legacy_pathway:
            yield
            return

        self._refresh_depth += 1
        try:
            yield
        except ConnectionError as ex:
            if self._refresh_fail_ex is None:
                self._refresh_fail_ex = ex
            raise
        finally:
            self._refresh_depth -= 1
            if self._refresh_depth == 0:
                pending = self._refresh_fail_ex
                self._refresh_fail_ex = None
                if pending is not None:
                    self._set_bridge_ok(False, pending)
                    self._nudge_scan()
                else:
                    self._set_bridge_ok(True)

    def _nudge_scan(self) -> None:
        """Non-blocking cooled-down IASD scan after HTTP transport failure."""
        if self._legacy_pathway or self._closed:
            return
        self._discovery_service.schedule_cooled_scan()

    # disposition: deprecate
    @classmethod
    def from_discovery(
        cls,
        discovery_service: DiscoveryService,
        event_coordinator: Listener,
        *,
        device_uid: str,
        device_ip: str,
        is_v2: bool,
        is_ipower: bool,
    ) -> Self:
        """Construct a controller for the legacy passive-discovery track.

        Prefer obtaining controllers through :func:`~pizone.discovery.discovery`
        and the listener / :meth:`~pizone.discovery.DiscoveryService.fetch_controller`
        path rather than calling this from application code. The caller must await
        :meth:`_initialize` before using the instance.
        """
        return cls(
            discovery_service,
            event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )

    # disposition: 1.4
    @classmethod
    async def create(
        cls,
        discovery_service: DiscoveryService,
        event_coordinator: Listener,
        *,
        endpoint: ControllerEndpoint,
        system_settings: Controller.ControllerData,
        on_address_changed: Callable[[ControllerEndpoint], None] | None = None,
    ) -> Self:
        """Create and initialize a controller from an HTTP probe result.

        Prefer :meth:`~pizone.discovery.DiscoveryService.create_controller` on a
        service from :func:`~pizone.discovery.create_discovery` rather than
        calling this from application code.
        """
        controller = cls(
            discovery_service,
            event_coordinator,
            device_uid=endpoint.uid,
            device_ip=endpoint.host,
            is_v2=False,
            is_ipower=power_mod.ENABLE_POWER,
        )
        controller._on_address_changed = on_address_changed
        await controller._initialize(system_settings=system_settings)
        return controller

    async def _initialize(
        self, system_settings: Controller.ControllerData | None = None
    ) -> None:
        """Load system, zone, and optional power data from the device.

        When *system_settings* is provided (1.4 create path), the initial
        SystemSettings GET is skipped and the probe payload is applied instead.

        Raises:
            ConnectionError: If a required HTTP request fails.
            KeyError: If a required field is missing from a device response.

        """
        async with self._refresh_scope():
            if system_settings is not None:
                settings = self._apply_system_settings(system_settings, notify=False)
            else:
                settings = await self._fetch_system(notify=False)
            if settings is None:
                raise ConnectionError("SystemSettings device ID mismatch")

            if self._system_settings:
                self.fan_modes = Controller._VALID_FAN_MODES[
                    str(self._system_settings.get("FanAuto", "disabled"))
                ]
            else:
                self.fan_modes = Controller._VALID_FAN_MODES["disabled"]

            await self._probe_v2_api()

            zone_count = int(settings["NoOfZones"])
            self.zones = [Zone(self, i) for i in range(zone_count)]

            await self._fetch_zones(notify=False)

            await self._probe_power()

            self._initialized = True
            if not self.connected:
                self._event_coordinator.controller_disconnected(
                    self, ConnectionError("iZone controller unavailable")
                )

        # disposition: deprecate — poll loop only on legacy pathway (removed in 1.4e)
        if self._legacy_pathway:
            self._discovery_service.create_task(self._poll_loop())

    async def _probe_v2_api(self) -> None:
        """Detect V2 API support; non-fatal on failure."""
        try:
            response = await self._http_post(
                "iZoneRequestV2",
                {"iZoneV2Request": {"Type": 1, "No": 0, "No1": 0}},
            )
            data = json.loads(response)
            uid = data["AirStreamDeviceUId"]
            self._is_v2 = uid == self._device_uid and "SystemV2" in data
        except ConnectionError, ControllerCommandError, json.JSONDecodeError, KeyError:
            self._is_v2 = False

    async def _probe_power(self) -> None:
        """Probe power monitor endpoint when discovery hinted iPower; non-fatal.

        No-op unless :data:`pizone.power.ENABLE_POWER` is ``True``.
        """
        if not power_mod.ENABLE_POWER:
            self._is_ipower = False
            self._power = None
            return

        if not self._is_ipower:
            self._power = None
            return

        try:
            power = Power(self)
            await power.init()
            if power.enabled:
                self._power = power
                return
            _LOG.warning(
                "Power monitor disabled on uid=%s; skipping power support",
                self._device_uid,
            )
        except ConnectionError, ControllerCommandError, json.JSONDecodeError, KeyError:
            _LOG.warning(
                "Power monitor probe failed for uid=%s",
                self._device_uid,
                exc_info=True,
            )

        self._is_ipower = False
        self._power = None

    # disposition: deprecate — legacy poll loop (not started on 1.4 path; removed in 1.4e)
    async def _poll_loop(self) -> None:
        while True:
            try:
                async with asyncio.timeout(Controller.REFRESH_INTERVAL):
                    async with self._scan_condition:
                        await self._scan_condition.wait()
                # triggered rescan, short delay
                await asyncio.sleep(Controller.UPDATE_REFRESH_DELAY)
            except TimeoutError:
                pass

            if self._closed or self._discovery_service.is_closed:
                return

            try:
                _LOG.debug("Polling unit %s.", self._device_uid)
                await self._refresh_all()
            except ConnectionError:
                _LOG.debug("Poll failed due to exception.", exc_info=True)
            except Exception:  # noqa: BLE001
                _LOG.error("Unexpected exception", exc_info=True)

    # disposition: deprecate — legacy poll wake only (removed in 1.4e)
    async def refresh(self) -> None:
        """Wake the legacy poll loop to refresh controller data.

        Raises:
            RuntimeError: If called on the 1.4 pathway. Use
                :meth:`refresh_all` / :meth:`refresh_system` / etc. instead.

        """
        if not self._legacy_pathway:
            raise RuntimeError(
                "Controller.refresh() is legacy-only; use refresh_all() "
                "or refresh_system() / refresh_zones() / refresh_power()"
            )
        async with self._scan_condition:
            self._scan_condition.notify()

    async def close(self) -> None:
        """Close the controller and release its UID back to discovery."""
        if self._closed:
            return
        self._closed = True
        self._on_address_changed = None
        self._discovery_service._controller_closed(self)  # noqa: SLF001
        async with self._scan_condition:
            self._scan_condition.notify()

    @property
    def bridge_connected(self) -> bool:
        """True while the ASH bridge is reachable over HTTP."""
        return self._bridge_ok

    @property
    def connected(self) -> bool:
        """True while the bridge is up and the iZone AC subsystem is available."""
        return self._bridge_ok and self._izone_ok

    @property
    def power(self) -> Power | None:
        """Power monitor data, or ``None`` if not configured."""
        return self._power

    @property
    def device_ip(self) -> str:
        """IP address of the unit."""
        return self._ip

    @property
    def device_uid(self) -> str:
        """UID of the unit."""
        return self._device_uid

    @property
    def is_v2(self) -> bool:
        """Return whether this is a v2 controller."""
        return self._is_v2

    @property
    def is_ipower(self) -> bool:
        """Return whether power monitoring is available."""
        return self._is_ipower

    @property
    def discovery(self) -> DiscoveryService:
        """Discovery service for this controller."""
        return self._discovery_service

    @property
    def is_on(self) -> bool:
        """Return whether the system is turned on."""
        return self._get_system_state("SysOn") == "on"

    async def set_on(self, value: bool) -> None:
        """Turn the system on or off.

        Raises:
            ConnectionError: If the device cannot be reached or the response is invalid.

        """
        await self._set_system_state("SysOn", "SystemON", "on" if value else "off")

    @property
    def mode(self) -> Mode:
        """System mode (cooling, heating, etc.)."""
        if self.free_air:
            return self.Mode.FREE_AIR
        try:
            return self.Mode(self._get_system_state("SysMode"))
        except ValueError:
            return self.Mode.COOL

    async def set_mode(self, value: Mode) -> None:
        """Set the system mode (cooling, heating, etc.).

        Raises:
            AttributeError: If free air mode is requested but not enabled.
            ConnectionError: If the device cannot be reached or the response is invalid.

        """
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
    def fan(self) -> Fan:
        """The current fan level."""
        try:
            return self.Fan(self._get_system_state("SysFan"))
        except ValueError:
            return self.Fan.AUTO

    async def set_fan(self, value: Fan) -> None:
        """Set the fan level.

        Not all fan modes are allowed depending on the system configuration.

        Raises:
            AttributeError: If the requested fan mode is not allowed.
            ConnectionError: If the device cannot be reached or the response is invalid.

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
        """Current setting for the sleep timer.

        Raises:
            TypeError: If the cached value is missing or not numeric.

        """
        return int(self._get_system_state("SleepTimer"))

    async def set_sleep_timer(self, value: int) -> None:
        """Set the sleep timer.

        Valid settings are 0, 30, 60, 90, and 120.

        Raises:
            AttributeError: If the value is out of range or not divisible by 30.
            ConnectionError: If the device cannot be reached or the response is invalid.

        """
        time = int(value)
        if time < 0 or time > 120 or time % 30 != 0:
            raise AttributeError(
                f'Invalid Sleep Timer "{value}", must be divisible by 30'
            )
        await self._set_system_state("SleepTimer", "SleepTimer", value, time)

    @property
    def free_air_enabled(self) -> bool:
        """Return whether the free air system is available."""
        return self._get_system_state("FreeAir") != "disabled"

    @property
    def free_air(self) -> bool:
        """Return whether the free air system is on."""
        return self._get_system_state("FreeAir") == "on"

    async def set_free_air(self, value: bool) -> None:
        """Turn the free air system on or off.

        Raises:
            AttributeError: If the free air system is not enabled.
            ConnectionError: If the device cannot be reached or the response is invalid.

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

        This is the unit target temperature when ``rasMode == RAS``, or when
        ``rasMode == master`` and ``ctrlZone == 13``.
        """
        return float(self._get_system_state("Setpoint")) or None

    async def set_temp_setpoint(self, value: float) -> None:
        """Set the AC unit setpoint temperature.

        This is the unit target temperature when ``rasMode == RAS``, or when
        ``rasMode == master`` and ``ctrlZone == 13``.

        Args:
            value: Valid settings are between ecoMin and ecoMax in 0.5 degree
                steps.

        Raises:
            AttributeError: If the value is out of range or not rounded to 0.5.
            ConnectionError: If the device cannot be reached or the response is invalid.

        """
        if value % 0.5 != 0:
            raise AttributeError(f"SetPoint '{value}' not rounded to nearest 0.5")
        if value < self.temp_min or value > self.temp_max:
            raise AttributeError(f"SetPoint '{value}' is out of range")
        await self._set_system_state("Setpoint", "UnitSetpoint", value, str(value))

    @property
    def temp_return(self) -> float | None:
        """Return air temperature."""
        return float(self._get_system_state("Temp")) or None

    @property
    def eco_lock(self) -> bool:
        """Return whether the eco lock setting is on."""
        return self._get_system_state("EcoLock") == "true"

    @property
    def temp_min(self) -> float:
        """Minimum temperature from eco lock, or 15 if eco lock is off."""
        return float(self._get_system_state("EcoMin")) if self.eco_lock else 15.0

    @property
    def temp_max(self) -> float:
        """Maximum temperature from eco lock, or 30 if eco lock is off."""
        return float(self._get_system_state("EcoMax")) if self.eco_lock else 30.0

    @property
    def ras_mode(self) -> str:
        """Return air sensor selection mode.

        Possible values are:

        ``master``: the AC unit is controlled from a manually selected CTS.
        ``RAS``: the AC unit is controlled from its own return air sensor.
        ``zones``: the AC unit is controlled from a CTS that is automatically
        selected based on the heating or cooling need of the zones.
        """
        return cast(str, self._get_system_state("RAS"))

    @property
    def zone_ctrl(self) -> int:
        """Zone that currently controls the AC unit.

        Value is interpreted in combination with :attr:`ras_mode`.

        Raises:
            TypeError: If the cached value is missing or not numeric.

        """
        return int(self._get_system_state("CtrlZone"))

    @property
    def zones_total(self) -> int:
        """Number of zones the system is configured for.

        Raises:
            TypeError: If the cached value is missing or not numeric.

        """
        return int(self._get_system_state("NoOfZones"))

    @property
    def zones_const(self) -> int:
        """Number of constant zones the system is configured for."""
        return int(self._get_system_state("NoOfConst"))

    @property
    def sys_type(self) -> str:
        """Type of the connected iZone system.

        Possible values are:

        ``110``: zone control only; all zones are open/close zones.
        ``210``: zone control only; zones may be temperature controlled.
        ``310``: zone control and unit control.
        """
        return cast(str, self._get_system_state("SysType"))

    async def _request_get(self, resource: str) -> Any:
        """GET used by refresh cores — bridge-aware on legacy, neutral on 1.4."""
        if self._legacy_pathway:
            return await self._get_resource(resource)
        return await self._http_get(resource)

    async def _gather_refresh(self, *coros: Awaitable[Any]) -> None:
        """Run refresh I/O concurrently; coalesce failures on the 1.4 path."""
        if self._legacy_pathway:
            await asyncio.gather(*coros)
            return
        results = await asyncio.gather(*coros, return_exceptions=True)
        await self._raise_gather_failures(results)

    async def _raise_gather_failures(self, results: list[Any]) -> None:
        """Raise the first transport failure, else the first other exception."""
        first_conn: ConnectionError | None = None
        first_other: BaseException | None = None
        for result in results:
            if isinstance(result, ConnectionError):
                if first_conn is None:
                    first_conn = result
            elif isinstance(result, BaseException):
                if first_other is None:
                    first_other = result
        if first_conn is not None:
            if self._refresh_fail_ex is None:
                self._refresh_fail_ex = first_conn
            raise first_conn
        if first_other is not None:
            raise first_other

    async def _fetch_system(self, notify: bool = True) -> ControllerData | None:
        """Fetch SystemSettings and apply to cache (no refresh scope)."""
        values: Controller.ControllerData = await self._request_get("SystemSettings")
        return self._apply_system_settings(values, notify=notify)

    async def _fetch_zone_group(self, group: int, notify: bool = True) -> None:
        """Fetch one zone group and update cache (no refresh scope)."""
        if group not in (0, 4, 8, 12):
            raise ValueError(f"Unsupported zone group start index {group}")

        resource = "Zones13_14" if group == 12 else f"Zones{group + 1}_{group + 4}"
        zone_data_part = await self._request_get(resource)

        for i in range(min(len(self.zones) - group, 4)):
            zone_data = zone_data_part[i]
            self.zones[i + group]._update_zone(zone_data, notify)  # noqa: SLF001

    async def _fetch_zones(self, notify: bool = True) -> None:
        """Fetch all zone groups with overlapped GETs (no refresh scope)."""
        zones = len(self.zones)
        if zones == 0:
            return
        await self._gather_refresh(
            *[self._fetch_zone_group(i, notify) for i in range(0, zones, 4)]
        )

    async def _fetch_power(self, notify: bool = True) -> None:
        """Fetch power monitor status when enabled (no refresh scope).

        Raises on failure — callers must handle. No-op when power is disabled
        or not probed.
        """
        if not power_mod.ENABLE_POWER:
            return
        if self._power is None or not self._power.enabled:
            return
        if not self.bridge_connected:
            return

        updated = await self._power.refresh()
        if updated and notify:
            self._event_coordinator.power_update(self)

    async def _fetch_all(self, notify: bool = True) -> None:
        """Overlapped system + zones [+ power] fetch (no refresh scope)."""
        zones = len(self.zones)
        coros: list[Awaitable[Any]] = [self._fetch_system(notify)]
        if power_mod.ENABLE_POWER and self._power is not None and self._power.enabled:
            coros.append(self._fetch_power(notify))
        if zones > 0:
            coros.extend(self._fetch_zone_group(i, notify) for i in range(0, zones, 4))
        await self._gather_refresh(*coros)

    @_refresh_api
    async def refresh_system(self) -> None:
        """Refresh system settings from the device via V1 HTTP."""
        await self._fetch_system(notify=True)

    @_refresh_api
    async def refresh_zones(self) -> None:
        """Refresh all zones from the device via V1 HTTP."""
        await self._fetch_zones(notify=True)

    @_refresh_api
    async def refresh_power(self) -> None:
        """Refresh power monitor data when enabled."""
        await self._fetch_power(notify=True)

    @_refresh_api
    async def refresh_all(self) -> None:
        """Refresh system, zones, and power (if enabled) via overlapping V1 GETs."""
        await self._fetch_all(notify=True)

    # disposition: deprecate — prefer public refresh_*; used by legacy poll
    async def _refresh_all(self, notify: bool = True) -> None:
        """Refresh system, power, and zone data from the device.

        Raises:
            ConnectionError: If any HTTP request fails.
            KeyError: If a required field is missing from a device response.

        """
        await self._fetch_all(notify=notify)

    # disposition: deprecate — prefer refresh_system()
    async def _refresh_system(self, notify: bool = True) -> ControllerData | None:
        """Refresh the system settings from the device.

        Returns the device response even when the payload is a fault placeholder,
        so callers can read fields like ``NoOfZones`` during initialization.

        Raises:
            ConnectionError: If the HTTP request fails.
            KeyError: If the response is missing required fields.

        """
        return await self._fetch_system(notify=notify)

    def _apply_system_settings(
        self, values: ControllerData, *, notify: bool = True
    ) -> ControllerData | None:
        """Apply SystemSettings payload to the local cache."""
        if self._device_uid != values["AirStreamDeviceUId"]:
            _LOG.error("_refresh_system called with non-matching device ID")
            return None

        if not self._system_settings_valid(values):
            _LOG.warning(
                "iZone subsystem fault uid=%s; retaining cache",
                self._device_uid,
            )
            self._set_izone_ok(False, ConnectionError("iZone controller unavailable"))
            if notify:
                self._event_coordinator.controller_update(self)
            return values

        self._system_settings = values
        self._set_izone_ok(True)
        if notify:
            self._event_coordinator.controller_update(self)
        return values

    # disposition: deprecate — prefer refresh_power()
    async def _refresh_power(self, notify: bool = True) -> None:
        """Refresh power monitor data when enabled."""
        await self._fetch_power(notify=notify)

    # disposition: deprecate — prefer refresh_zones()
    async def _refresh_zones(self, notify: bool = True) -> None:
        """Refresh all zone groups from the device."""
        await self._fetch_zones(notify=notify)

    # disposition: deprecate
    async def _refresh_zone_group(self, group: int, notify: bool = True) -> None:
        """Refresh one zone group from the device."""
        await self._fetch_zone_group(group, notify=notify)

    def _refresh_address(self, address: str) -> None:
        """Update the device IP and schedule a reconnect attempt if needed."""
        if self._closed:
            return
        if address != self._ip:
            self._ip = address
            if self._on_address_changed is not None:
                endpoint = ControllerEndpoint(uid=self._device_uid, host=address)
                self._discovery_service.schedule_address_changed(
                    self._on_address_changed, endpoint
                )
        # disposition: deprecate — retry task only on legacy pathway
        if not self._bridge_ok and self._legacy_pathway:
            self._discovery_service.create_task(self._retry_connection())

    @staticmethod
    def _system_settings_valid(values: ControllerData) -> bool:
        """Return whether *values* look like healthy AC subsystem data."""
        return (
            int(values["NoOfZones"]) > 0
            and str(values["SysFan"]) != "error"
            and str(values["RAS"]) != "error"
        )

    def _get_system_state(self, state: str) -> DictValue:
        default = self._SYSTEM_STATE_DEFAULTS.get(state, "")
        value = self._system_settings.get(state)
        if value is None:
            return default
        if (
            state in ("SysFan", "RAS", "SysMode")
            and str(value) in self._FAULT_STATE_VALUES
        ):
            return default
        return value

    async def _set_system_state(
        self,
        state: str,
        command: str,
        value: DictValue,
        send: DictValue | None = None,
    ) -> None:
        """Send a system command and update the local cache.

        Raises:
            ConnectionError: If the HTTP request fails.

        """
        if send is None:
            send = value

        if self._legacy_pathway:
            await self._send_command_async(command, {command: send})
            self._system_settings[state] = value
            self._event_coordinator.controller_update(self)
            await self.refresh()
            return

        async with self._sending_lock, self._refresh_scope():
            await self._http_post(command, {command: send})
            self._system_settings[state] = value
            self._event_coordinator.controller_update(self)
            await self._fetch_system(notify=True)

    async def _execute_zone_command(
        self, command: str, data: dict[str, Any], zone_index: int
    ) -> None:
        """POST a zone command; on 1.4 path confirm with that zone group's GET."""
        if self._legacy_pathway:
            await self._send_command_async(command, data)
            return

        group = (zone_index // 4) * 4
        async with self._sending_lock, self._refresh_scope():
            await self._http_post(command, data)
            await self._fetch_zone_group(group, notify=True)

    def _set_bridge_ok(self, ok: bool, ex: Exception | None = None) -> None:
        was_connected = self.connected
        self._bridge_ok = ok
        if not ok and ex is None:
            ex = ConnectionError("Unable to connect to the controller")
        self._notify_connected_changed(was_connected, ex if not ok else None)

    def _set_izone_ok(self, ok: bool, ex: Exception | None = None) -> None:
        was_connected = self.connected
        self._izone_ok = ok
        if not ok and ex is None:
            ex = ConnectionError("iZone controller unavailable")
        self._notify_connected_changed(was_connected, ex if not ok else None)

    def _notify_connected_changed(
        self, was_connected: bool, ex: Exception | None
    ) -> None:
        if was_connected == self.connected or not self._initialized:
            return
        if self.connected:
            self._event_coordinator.controller_update(self)
            for zone in self.zones:
                self._event_coordinator.zone_update(self, zone)
            self._event_coordinator.power_update(self)
            self._event_coordinator.controller_reconnected(self)
        elif ex is not None:
            self._event_coordinator.controller_disconnected(self, ex)

    def _failed_connection(self, ex: Exception) -> None:
        """Mark the bridge transport as failed."""
        self._set_bridge_ok(False, ex)

    def _restored_connection(self) -> None:
        """Mark the bridge transport as restored."""
        self._set_bridge_ok(True)

    # disposition: deprecate — legacy reconnect after UDP address change
    async def _retry_connection(self) -> None:
        """Attempt to restore connectivity after a connection failure.

        Connection errors are logged and not re-raised. A successful refresh
        restores :attr:`connected` when both bridge and iZone layers recover.
        """
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

    async def _http_get(self, resource: str) -> Any:
        """Fetch a JSON resource via HTTP GET without updating connection state.

        On the legacy pathway, serializes via ``_sending_lock`` (1.3 behaviour).
        On the 1.4 pathway, does not lock so overlapped GETs can run concurrently;
        command micro-batches hold the lock around POST + confirm separately.

        Raises:
            ConnectionError: If the device cannot be reached.
            ResponseDecodeError: If the response cannot be decoded as JSON.
            ControllerCommandError: If the device returns HTTP 4xx.

        """
        session = self._discovery_service.session
        if session is None:
            raise ConnectionError("Discovery service is not started")
        lock = self._sending_lock if self._legacy_pathway else nullcontext()
        try:
            async with (
                lock,
                session.get(
                    f"http://{self.device_ip}/{resource}",
                    headers={"Connection": "close"},
                    timeout=aiohttp.ClientTimeout(total=Controller.REQUEST_TIMEOUT),
                ) as response,
            ):
                if response.status >= 400 and response.status < 500:
                    raise ControllerCommandError(
                        f"HTTP {response.status} for http://{self.device_ip}/{resource}"
                    )
                try:
                    return await response.json(content_type=None)
                except json.JSONDecodeError as ex:
                    text = await response.text()
                    if text[-4:] == "{OK}":
                        return json.loads(text[:-4])
                    _LOG.error('Decode error for "%s"', text, exc_info=True)
                    raise ResponseDecodeError(
                        "Unable to decode response from the controller"
                    ) from ex
        except (TimeoutError, aiohttp.ClientError) as ex:
            raise ConnectionError("Unable to connect to the controller") from ex

    async def _http_post(self, command: str, data: dict[str, Any]) -> str:
        """Send a command via HTTP POST without updating connection state.

        On the legacy pathway, serializes via ``_sending_lock`` (1.3 behaviour).
        On the 1.4 pathway, does not lock here — callers that need POST+confirm
        atomicity hold ``_sending_lock`` around the micro-batch.

        Raises:
            ConnectionError: If the device cannot be reached or the HTTP request fails.
            ControllerCommandError: If the device returns HTTP 4xx or an
                ``{ERROR...}`` payload.

        """
        session = self._discovery_service.session
        if session is None:
            raise ConnectionError("Discovery service is not started")
        body = json.dumps(data).encode("latin_1")
        lock = self._sending_lock if self._legacy_pathway else nullcontext()
        try:
            async with (
                lock,
                session.post(
                    f"http://{self.device_ip}/{command}",
                    data=body,
                    timeout=aiohttp.ClientTimeout(total=Controller.REQUEST_TIMEOUT),
                ) as response,
            ):
                if response.status >= 400 and response.status < 500:
                    raise ControllerCommandError(
                        f"HTTP {response.status} for http://{self.device_ip}/{command}"
                    )
                if response.status != 200:
                    raise ConnectionError(
                        f"Unable to connect to: http://{self.device_ip}/{command}"
                        f" response={response.status} message={response.reason}"
                    )
                result = await response.text(encoding="latin_1")
        except (TimeoutError, aiohttp.ClientError) as ex:
            raise ConnectionError("Unable to connect to the controller") from ex

        if len(result) >= 7 and result[:6] == "{ERROR":
            raise ControllerCommandError(f"Server returned error state {result}")
        if len(result) >= 4 and result[-4:] == "{OK}":
            result = result[:-4]
        return result

    async def _get_resource(self, resource: str) -> Any:
        """Fetch a JSON resource from the device via HTTP GET.

        Raises:
            ConnectionError: If the device cannot be reached or the HTTP request fails.
            ResponseDecodeError: If the response cannot be decoded as JSON.
            ControllerCommandError: If the device returns HTTP 4xx.

        """
        try:
            result = await self._http_get(resource)
        except ControllerCommandError:
            self._set_bridge_ok(True)
            raise
        except ResponseDecodeError:
            self._set_bridge_ok(True)
            raise
        except ConnectionError as ex:
            self._set_bridge_ok(False, ex)
            raise
        self._set_bridge_ok(True)
        return result

    async def _send_command_async(self, command: str, data: dict[str, Any]) -> str:
        """Send a command to the device via HTTP POST.

        Raises:
            ConnectionError: If the device cannot be reached or the HTTP request fails.
            ControllerCommandError: If the device returns HTTP 4xx or an
                ``{ERROR...}`` payload.

        """
        try:
            result = await self._http_post(command, data)
        except ControllerCommandError:
            self._set_bridge_ok(True)
            raise
        except ConnectionError as ex:
            self._set_bridge_ok(False, ex)
            raise
        self._set_bridge_ok(True)
        return result
