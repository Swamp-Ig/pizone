"""Tests for controller property reads and command paths."""

import asyncio
from copy import deepcopy
from typing import cast
from unittest.mock import AsyncMock

import pytest

from pizone import Controller, Zone, v2 as v2_mod
from pizone.controller import ControllerCommandError

from .conftest import MockController, MockDiscoveryService
from .power_data import POWER_CONFIG
from .resources import (
    FAULT_SYSTEM_SETTINGS_BRIDGE_NO_AC,
    FAULT_SYSTEM_SETTINGS_PAIRED_COLD_RESTART,
    SYSTEMS,
)
from .v2_fixtures import SAMPLE_SYSTEM_V2, SAMPLE_ZONES_V2


def _fault_system_settings(device_uid: str) -> dict[str, object]:
    settings = deepcopy(SYSTEMS["000000001"]["SystemSettings"])
    settings["AirStreamDeviceUId"] = device_uid
    settings["NoOfZones"] = 0
    settings["SysFan"] = "error"
    settings["RAS"] = "error"
    settings["UnitType"] = "No Unit Type Configured!"
    return settings


def _zero_zone_system_settings(device_uid: str) -> dict[str, object]:
    settings = deepcopy(SYSTEMS["000000001"]["SystemSettings"])
    settings.update(
        {
            "AirStreamDeviceUId": device_uid,
            "SysOn": "off",
            "SysMode": "cool",
            "SysFan": "low",
            "Setpoint": "22.0",
            "RAS": "RAS",
            "CtrlZone": 15,
            "NoOfConst": 0,
            "NoOfZones": 0,
            "FanAuto": "disabled",
        }
    )
    return settings


@pytest.mark.asyncio
async def test_controller_property_reads(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    assert controller.connected is True
    assert controller.power is None
    assert controller.device_ip == "8.8.8.8"
    assert controller.device_uid == "000000001"
    assert controller.is_v2 is False
    assert controller.is_on is True
    assert controller.mode == Controller.Mode.HEAT
    assert controller.fan == Controller.Fan.AUTO
    assert controller.sleep_timer == 0
    assert controller.free_air_enabled is True
    assert controller.free_air is False
    assert controller.temp_supply == pytest.approx(25.1)
    assert controller.temp_setpoint == pytest.approx(23.5)
    assert controller.temp_return == pytest.approx(23.6)
    assert controller.eco_lock is True
    assert controller.temp_min == pytest.approx(15.0)
    assert controller.temp_max == pytest.approx(30.0)
    assert controller.ras_mode == "zones"
    assert controller.zone_ctrl == 1
    assert controller.zones_total == 8
    assert controller.zones_const == 1
    assert controller.sys_type == "320"


@pytest.mark.asyncio
async def test_set_on(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    await controller.set_on(False)
    assert controller.is_on is False
    assert controller.sent[-1] == ("SystemON", {"SystemON": "off"})

    await controller.set_on(True)
    assert controller.is_on is True


@pytest.mark.asyncio
async def test_set_fan(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))
    fan = Controller.Fan.LOW

    await controller.set_fan(fan)
    assert controller.fan == fan
    assert controller.sent[-1] == ("SystemFAN", {"SystemFAN": "low"})


@pytest.mark.asyncio
async def test_set_sleep_timer(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    await controller.set_sleep_timer(30)
    assert controller.sleep_timer == 30

    await controller.set_sleep_timer(0)
    assert controller.sleep_timer == 0


@pytest.mark.asyncio
async def test_set_temp_setpoint(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    await controller.set_temp_setpoint(22.5)
    assert controller.temp_setpoint == pytest.approx(22.5)


@pytest.mark.asyncio
async def test_set_fan_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    with pytest.raises(AttributeError, match="Fan mode top not allowed"):
        await controller.set_fan(Controller.Fan.TOP)


@pytest.mark.asyncio
async def test_set_sleep_timer_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    with pytest.raises(AttributeError, match="Invalid Sleep Timer"):
        await controller.set_sleep_timer(45)


@pytest.mark.asyncio
async def test_set_temp_setpoint_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    with pytest.raises(AttributeError, match="not rounded to nearest 0.5"):
        await controller.set_temp_setpoint(23.3)
    with pytest.raises(AttributeError, match="out of range"):
        await controller.set_temp_setpoint(35.0)


@pytest.mark.asyncio
async def test_power_disabled_by_default_skips_power_request() -> None:
    """With ENABLE_POWER False, an iPower hint must not trigger PowerRequest."""
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000003",
        device_ip="10.0.0.1",
        is_v2=False,
        is_ipower=True,
    )
    svc._claim(controller)

    try:
        await controller._initialize(controller.resources["SystemSettings"])

        assert controller.power is None
        assert controller.is_ipower is False
        assert not any(command == "PowerRequest" for command, _ in controller.sent)

        await controller.refresh_power()
        assert not any(command == "PowerRequest" for command, _ in controller.sent)
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_power")
async def test_initialize_succeeds_when_power_init_fails() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000003",
        device_ip="10.0.0.1",
        is_v2=False,
        is_ipower=True,
    )
    controller.fail_power_types.add(1)
    svc._claim(controller)

    try:
        await controller._initialize(controller.resources["SystemSettings"])

        assert controller.connected is True
        assert controller.power is None
        assert controller.is_ipower is False
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_initialize_sets_power_when_ipower_works(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controller("000000003"))

    assert controller.power is not None
    assert controller.power.enabled is True
    assert controller.is_ipower is True


@pytest.mark.asyncio
async def test_initialize_does_not_fetch_power_status(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controller("000000003"))
    power_requests = [
        data for command, data in controller.sent if command == "PowerRequest"
    ]

    assert len(power_requests) == 1
    assert power_requests[0]["PowerRequest"]["Type"] == 1

    await controller.refresh_power()

    status_requests = [
        data
        for command, data in controller.sent
        if command == "PowerRequest" and data["PowerRequest"]["Type"] == 2
    ]
    assert len(status_requests) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_power")
async def test_initialize_clears_ipower_when_config_disabled() -> None:
    disabled_config = deepcopy(POWER_CONFIG)
    disabled_config["Enabled"] = 0

    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000003",
        device_ip="10.0.0.1",
        is_v2=False,
        is_ipower=True,
    )
    controller.power_config = disabled_config
    svc._claim(controller)

    try:
        await controller._initialize(controller.resources["SystemSettings"])

        assert controller.power is None
        assert controller.is_ipower is False
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_power")
async def test_power_init_probe_failure_leaves_controller_connected() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000003",
        device_ip="10.0.0.1",
        is_v2=False,
        is_ipower=True,
    )
    controller.fail_power_types.add(1)
    svc._claim(controller)

    try:
        await controller._initialize(controller.resources["SystemSettings"])

        assert controller.connected is True
        assert controller.bridge_connected is True
        assert controller._bridge_ok is True
        assert controller._izone_ok is True
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_power_refresh_skipped_when_bridge_disconnected(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controller("000000003"))
    assert controller.power is not None
    power_requests_before = [
        data for command, data in controller.sent if command == "PowerRequest"
    ]
    controller._bridge_ok = False

    await controller.refresh_power()

    power_requests_after = [
        data for command, data in controller.sent if command == "PowerRequest"
    ]
    assert power_requests_after == power_requests_before


@pytest.mark.asyncio
async def test_power_refresh_transport_failure_marks_bridge_down(
    ipower_service: MockDiscoveryService,
) -> None:
    """Power refresh shares the bridge, so its transport failure settles bridge_ok."""
    controller = cast(MockController, ipower_service._controller("000000003"))
    assert controller.power is not None
    controller.fail_power_types.add(2)

    with pytest.raises(ConnectionError):
        await controller.refresh_power()

    assert controller.bridge_connected is False
    assert controller.power.connected is False


@pytest.mark.asyncio
async def test_zero_zone_system_initializes_controller() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000004",
        device_ip="10.0.0.4",
        is_v2=False,
        is_ipower=False,
    )
    settings = _zero_zone_system_settings(controller.device_uid)
    controller.resources["SystemSettings"] = settings
    svc._claim(controller)

    try:
        await controller._initialize(controller.resources["SystemSettings"])

        assert controller.bridge_connected is True
        assert controller.connected is True
        assert controller.dump_state()["system_settings"] == settings
        assert controller.is_on is False
        assert controller.mode == Controller.Mode.COOL
        assert controller.fan == Controller.Fan.LOW
        assert controller.temp_setpoint == pytest.approx(22.0)
        assert controller.zones_total == 0
        assert controller.zones == []
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_negative_zone_count_marks_izone_not_ok() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000004",
        device_ip="10.0.0.4",
        is_v2=False,
        is_ipower=False,
    )
    settings = _zero_zone_system_settings(controller.device_uid)
    settings["NoOfZones"] = -1
    controller.resources["SystemSettings"] = settings
    svc._claim(controller)

    try:
        await controller._initialize(controller.resources["SystemSettings"])

        assert controller.bridge_connected is True
        assert controller.connected is False
        assert controller.dump_state()["system_settings"] == {}
        assert controller.zones == []
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_bridge_ok_true_when_izone_fault(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))
    healthy_fan = controller.fan

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller.refresh_system()

    assert controller.bridge_connected is True
    assert controller.connected is False
    assert controller.fan == healthy_fan


@pytest.mark.parametrize(
    "fault_settings",
    [
        pytest.param(
            FAULT_SYSTEM_SETTINGS_BRIDGE_NO_AC,
            id="unpaired_bridge_no_ac",
        ),
        pytest.param(
            FAULT_SYSTEM_SETTINGS_PAIRED_COLD_RESTART,
            id="paired_cold_restart",
        ),
    ],
)
@pytest.mark.asyncio
async def test_live_fault_fixtures_mark_izone_not_ok(
    service: MockDiscoveryService,
    fault_settings: dict[str, object],
) -> None:
    """Preserve live unpaired and paired cold-restart fault specimens."""
    controller = cast(MockController, service._controller("000000001"))
    settings = deepcopy(fault_settings)
    settings["AirStreamDeviceUId"] = controller.device_uid
    controller.resources["SystemSettings"] = settings
    await controller.refresh_system()
    assert controller.bridge_connected is True
    assert controller.connected is False


@pytest.mark.asyncio
async def test_cache_preserved_on_izone_fault(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))
    healthy_settings = deepcopy(controller.resources["SystemSettings"])

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller.refresh_system()

    assert controller._system_settings == healthy_settings


@pytest.mark.asyncio
async def test_connected_restored_on_izone_recovery(
    service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, service._controller("000000001"))
    healthy_settings = deepcopy(controller.resources["SystemSettings"])

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller.refresh_system()
    assert controller.connected is False

    controller.resources["SystemSettings"] = healthy_settings
    await controller.refresh_system()
    assert controller.connected is True


@pytest.mark.asyncio
async def test_init_fault_marks_disconnected() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000004",
        device_ip="10.0.0.4",
        is_v2=False,
        is_ipower=False,
    )
    controller.resources["SystemSettings"] = _fault_system_settings("000000004")

    await controller._initialize(controller.resources["SystemSettings"])

    assert controller.connected is False
    assert controller.zones == []


@pytest.mark.asyncio
async def test_init_fault_property_reads_no_crash() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000005",
        device_ip="10.0.0.5",
        is_v2=False,
        is_ipower=False,
    )
    controller.resources["SystemSettings"] = _fault_system_settings("000000005")

    await controller._initialize(controller.resources["SystemSettings"])

    assert controller.connected is False
    assert controller.ras_mode == "zones"
    assert controller.sys_type == "0"
    assert controller.fan == Controller.Fan.AUTO
    assert controller.mode == Controller.Mode.COOL
    assert controller.is_on is False
    assert controller.zones_total == 0


@pytest.mark.asyncio
async def test_fault_placeholder_values_in_cache_use_defaults(
    service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, service._controller("000000001"))
    controller._system_settings["SysFan"] = "error"
    controller._system_settings["RAS"] = "error"
    controller._system_settings["SysMode"] = "error"

    assert controller.fan == Controller.Fan.AUTO
    assert controller.ras_mode == "zones"
    assert controller.mode == Controller.Mode.COOL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "prop"),
    [
        ("SysFan", "quiet", "fan"),
        ("SysFan", "weird", "fan"),
        ("SysMode", "quiet", "mode"),
        ("SysMode", "turbo", "mode"),
    ],
)
async def test_unknown_sys_enum_raises(
    service: MockDiscoveryService,
    field: str,
    value: str,
    prop: str,
) -> None:
    """Unknown non-fault SysFan/SysMode must not silently map to AUTO/COOL."""
    controller = cast(MockController, service._controller("000000001"))
    controller._system_settings[field] = value

    with pytest.raises(ValueError):
        getattr(controller, prop)


@pytest.mark.asyncio
async def test_unknown_fan_auto_falls_back_to_unknown_list() -> None:
    service = MockDiscoveryService()
    controller = MockController(
        service,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    settings = deepcopy(controller.resources["SystemSettings"])
    settings["FanAuto"] = "brand-new-speed-table"
    controller.resources["SystemSettings"] = settings
    service._claim(controller)

    await controller._initialize(system_settings=settings)

    assert controller.fan_modes == Controller._VALID_FAN_MODES["unknown"]
    await service.close()


@pytest.mark.asyncio
async def test_both_false_on_transport_failure(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))
    controller._connected = False

    with pytest.raises(ConnectionError):
        await controller.refresh_system()

    assert controller.bridge_connected is False
    assert controller.connected is False


@pytest.mark.asyncio
async def test_create_controller_starts_no_long_running_tasks() -> None:
    service = MockDiscoveryService()
    controller = MockController(
        service,
        device_uid="000000001",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    settings = deepcopy(controller.resources["SystemSettings"])
    service._claim(controller)

    await controller._initialize(system_settings=settings)

    living = [task for task in service._tasks if not task.done()]
    assert living == []
    await service.close()


@pytest.mark.asyncio
async def test_start_discovery_starts_no_background_tasks() -> None:
    service = MockDiscoveryService()
    await service.start_discovery()
    living = [task for task in service._tasks if not task.done()]
    assert living == []
    await service.close()


@pytest.mark.asyncio
async def test_refresh_all_public_updates_cache(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))
    controller.resources["SystemSettings"]["SysMode"] = "cool"

    await controller.refresh_all()

    assert controller.mode == Controller.Mode.COOL


@pytest.mark.asyncio
async def test_refresh_transport_failure_nudges_scan() -> None:
    service = MockDiscoveryService()
    controller = MockController(
        service,
        device_uid="000000001",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    settings = deepcopy(controller.resources["SystemSettings"])
    service._claim(controller)
    await controller._initialize(system_settings=settings)

    controller._connected = False
    scan = AsyncMock()
    service.scan = scan  # type: ignore[method-assign]

    with pytest.raises(ConnectionError):
        await controller.refresh_all()

    await asyncio.sleep(0)
    scan.assert_awaited_once()
    assert controller.bridge_connected is False
    await service.close()


@pytest.mark.asyncio
async def test_refresh_failure_scan_respects_cooldown() -> None:
    service = MockDiscoveryService()
    controller = MockController(
        service,
        device_uid="000000001",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    settings = deepcopy(controller.resources["SystemSettings"])
    service._claim(controller)
    await controller._initialize(system_settings=settings)

    controller._connected = False
    scan = AsyncMock()
    service.scan = scan  # type: ignore[method-assign]

    with pytest.raises(ConnectionError):
        await controller.refresh_system()
    await asyncio.sleep(0)
    with pytest.raises(ConnectionError):
        await controller.refresh_system()
    await asyncio.sleep(0)

    scan.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_set_system_command_confirms_with_refresh_system() -> None:
    service = MockDiscoveryService()
    controller = MockController(
        service,
        device_uid="000000001",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    settings = deepcopy(controller.resources["SystemSettings"])
    service._claim(controller)
    await controller._initialize(system_settings=settings)

    fetch = AsyncMock(wraps=controller._fetch_system)
    controller._fetch_system = fetch  # type: ignore[method-assign]

    await controller.set_on(False)

    fetch.assert_awaited()
    assert any(command == "SystemON" for command, _ in controller.sent)
    await service.close()


@pytest.mark.asyncio
async def test_zone_command_confirms_with_zone_group_refresh() -> None:
    service = MockDiscoveryService()
    controller = MockController(
        service,
        device_uid="000000001",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    settings = deepcopy(controller.resources["SystemSettings"])
    service._claim(controller)
    await controller._initialize(system_settings=settings)

    fetch = AsyncMock(wraps=controller._fetch_zone_group)
    controller._fetch_zone_group = fetch  # type: ignore[method-assign]

    await controller.zones[1].set_temp_setpoint(22.0)

    fetch.assert_awaited()
    assert fetch.await_args.args[0] == 0
    await service.close()


@pytest.mark.asyncio
async def test_v2_refresh_sequential_zones_keeps_battery() -> None:
    """V2 path uses sequential Type=2 (no gather — bridge queues concurrent waits)."""
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000025841",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    controller._read_api = "v2"
    controller._is_v2 = True
    controller._v2_use_content_type = False
    controller.v2_system = SAMPLE_SYSTEM_V2
    controller.v2_zones = SAMPLE_ZONES_V2
    settings = v2_mod.system_v2_to_settings(SAMPLE_SYSTEM_V2["SystemV2"], "000025841")
    svc._claim(controller)
    await controller._initialize(system_settings=settings)

    assert controller.is_v2 is True
    assert controller._read_api == "v2"
    assert controller.zones[0].dump_state()["BattVolt"] == 1

    controller.sent.clear()
    controller.v2_zones = {
        0: {
            **SAMPLE_ZONES_V2[0],
            "ZonesV2": {**SAMPLE_ZONES_V2[0]["ZonesV2"], "BattVolt": 280},
        },
        1: SAMPLE_ZONES_V2[1],
    }
    await controller.refresh_zones()
    assert controller.zones[0].dump_state()["BattVolt"] == 280

    zone_posts = [
        item
        for item in controller.sent
        if item[0] == "iZoneRequestV2" and item[1]["iZoneV2Request"]["Type"] == 2
    ]
    assert [p[1]["iZoneV2Request"]["No"] for p in zone_posts] == [0, 1]
    await svc.close()


@pytest.mark.asyncio
async def test_v2_system_commands_use_izone_command_v2() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000025841",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    controller._read_api = "v2"
    controller._is_v2 = True
    controller._v2_use_content_type = False
    controller.v2_system = SAMPLE_SYSTEM_V2
    controller.v2_zones = SAMPLE_ZONES_V2
    settings = v2_mod.system_v2_to_settings(SAMPLE_SYSTEM_V2["SystemV2"], "000025841")
    svc._claim(controller)
    await controller._initialize(system_settings=settings)
    controller.sent.clear()

    await controller.set_on(True)
    await controller.set_mode(Controller.Mode.COOL)
    await controller.set_fan(Controller.Fan.AUTO)
    await controller.set_temp_setpoint(22.5)

    commands = [item for item in controller.sent if item[0] == "iZoneCommandV2"]
    assert {"SysOn": 1} in [c[1] for c in commands]
    assert {"SysMode": 1} in [c[1] for c in commands]
    assert {"SysFan": 4} in [c[1] for c in commands]
    assert {"SysSetpoint": 2250} in [c[1] for c in commands]
    assert not any(cmd == "SystemON" for cmd, _ in controller.sent)
    await svc.close()


@pytest.mark.asyncio
async def test_v2_zone_commands_confirm_with_type2() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000025841",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    controller._read_api = "v2"
    controller._is_v2 = True
    controller._v2_use_content_type = False
    controller.v2_system = SAMPLE_SYSTEM_V2
    controller.v2_zones = deepcopy(SAMPLE_ZONES_V2)
    settings = v2_mod.system_v2_to_settings(SAMPLE_SYSTEM_V2["SystemV2"], "000025841")
    svc._claim(controller)
    await controller._initialize(system_settings=settings)
    controller.sent.clear()

    await controller.zones[0].set_temp_setpoint(22.0)
    controller.v2_zones[0]["ZonesV2"]["Mode"] = 2
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    # Mode confirmation now runs after ACK, independently of service completion.
    assert controller._zone_confirm_task is not None
    await asyncio.wait_for(controller._zone_confirm_task, 2)
    await controller.zones[0].set_airflow_min(20)

    assert ("iZoneCommandV2", {"ZoneSetpoint": {"Index": 0, "Setpoint": 2200}}) in (
        controller.sent
    )
    assert ("iZoneCommandV2", {"ZoneMode": {"Index": 0, "Mode": 2}}) in controller.sent
    assert ("iZoneCommandV2", {"ZoneMinAir": {"Index": 0, "MinAir": 20}}) in (
        controller.sent
    )
    confirm = [
        item
        for item in controller.sent
        if item[0] == "iZoneRequestV2" and item[1]["iZoneV2Request"]["Type"] == 2
    ]
    assert all(item[1]["iZoneV2Request"]["No"] == 0 for item in confirm)
    assert len(confirm) >= 3
    assert not any(cmd == "ZoneCommand" for cmd, _ in controller.sent)
    await svc.close()


async def _v2_free_air_controller() -> tuple[MockDiscoveryService, MockController]:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000025841",
        device_ip="10.0.0.90",
        is_v2=False,
        is_ipower=False,
    )
    controller._read_api = "v2"
    controller._is_v2 = True
    controller.v2_system = SAMPLE_SYSTEM_V2
    controller.v2_zones = SAMPLE_ZONES_V2
    settings = v2_mod.system_v2_to_settings(SAMPLE_SYSTEM_V2["SystemV2"], "000025841")
    # FreeAir must be available for the setter path.
    settings["FreeAir"] = "off"
    svc._claim(controller)
    await controller._initialize(system_settings=settings)
    controller.sent.clear()
    return svc, controller


@pytest.mark.asyncio
async def test_v2_free_air_uses_isave_when_available() -> None:
    svc, controller = await _v2_free_air_controller()

    await controller.set_free_air(True)
    assert ("iZoneCommandV2", {"iSaveOn": 1}) in controller.sent
    assert not any(cmd == "FreeAir" for cmd, _ in controller.sent)
    assert controller._isave_v2_unsupported is False
    await svc.close()


@pytest.mark.asyncio
async def test_v2_free_air_falls_back_and_stays_on_v1() -> None:
    """iSaveOn is documented but confirmed non-functional on real hardware.

    Try it (V2-only units are untested), but once it errors, remember that
    for this controller so later calls skip straight to V1.
    """
    svc, controller = await _v2_free_air_controller()
    controller._http_command_v2 = AsyncMock(  # type: ignore[method-assign]
        side_effect=ControllerCommandError("Server returned error state {ERROR}")
    )

    await controller.set_free_air(True)
    assert ("FreeAir", {"FreeAir": "on"}) in controller.sent
    assert controller._isave_v2_unsupported is True

    controller.sent.clear()
    controller._http_command_v2.reset_mock()
    await controller.set_free_air(False)
    assert ("FreeAir", {"FreeAir": "off"}) in controller.sent
    controller._http_command_v2.assert_not_awaited()
    await svc.close()


@pytest.mark.asyncio
async def test_v2_other_commands_do_not_fall_back_on_command_error() -> None:
    """Only FreeAir's known-unreliable V2 command gets a V1 fallback."""
    svc, controller = await _v2_free_air_controller()
    controller._http_command_v2 = AsyncMock(  # type: ignore[method-assign]
        side_effect=ControllerCommandError("Server returned error state {ERROR}")
    )

    with pytest.raises(ControllerCommandError):
        await controller.set_sleep_timer(30)
    assert not any(cmd == "SleepTimer" for cmd, _ in controller.sent)
    await svc.close()


@pytest.mark.asyncio
async def test_v1_status_properties(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))

    assert controller.airflow_lock is False
    assert controller.airflow_min_lock is True
    assert controller.system_locked is False
    assert controller.indoor_humidity is None
    assert controller.indoor_tvoc is None
    assert controller.indoor_eco2 is None


@pytest.mark.asyncio
async def test_v2_status_properties(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controller("000000001"))
    controller._system_settings = v2_mod.system_v2_to_settings(
        SAMPLE_SYSTEM_V2["SystemV2"], "000000001"
    )

    assert controller.airflow_lock is False
    assert controller.airflow_min_lock is False
    assert controller.system_locked is False
    assert controller.indoor_humidity == 50
    assert controller.indoor_tvoc == 0
    assert controller.indoor_eco2 == 400


@pytest.mark.parametrize(
    ("cached", "airflow_lock", "airflow_min_lock"),
    [
        pytest.param({"AirflowLock": "on"}, True, True, id="on"),
        pytest.param({"AirflowLock": "onMin"}, False, True, id="on-min"),
        pytest.param({"AirflowLock": "off"}, False, False, id="off"),
        pytest.param({"AirflowLock": "weird"}, None, None, id="unknown"),
        pytest.param({}, None, None, id="absent"),
    ],
)
@pytest.mark.asyncio
async def test_airflow_lock_decoding(
    service: MockDiscoveryService,
    cached: dict[str, str],
    airflow_lock: bool | None,
    airflow_min_lock: bool | None,
) -> None:
    controller = cast(MockController, service._controller("000000001"))
    controller._system_settings.pop("AirflowLock")
    controller._system_settings.update(cached)

    assert controller.airflow_lock is airflow_lock
    assert controller.airflow_min_lock is airflow_min_lock


@pytest.mark.parametrize(
    ("cached", "expected"),
    [
        pytest.param({"UnitLocked": "true"}, True, id="locked"),
        pytest.param({"UnitLocked": "false"}, False, id="unlocked"),
        pytest.param({}, None, id="absent"),
    ],
)
@pytest.mark.asyncio
async def test_system_locked_decoding(
    service: MockDiscoveryService, cached: dict[str, str], expected: bool | None
) -> None:
    controller = cast(MockController, service._controller("000000001"))
    controller._system_settings.pop("UnitLocked")
    controller._system_settings.update(cached)

    assert controller.system_locked is expected
