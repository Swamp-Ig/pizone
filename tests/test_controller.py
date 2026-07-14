"""Tests for controller property reads and command paths."""

from copy import deepcopy
from typing import cast

import pytest

from pizone import Controller, Listener

from .conftest import MockController, MockDiscoveryService, _register_mock_service
from .power_data import POWER_CONFIG
from .resources import SYSTEMS


class _DisconnectListener(Listener):
    def __init__(self) -> None:
        self.disconnected = 0
        self.reconnected = 0
        self.last_exception: Exception | None = None

    def controller_disconnected(self, _ctrl: Controller, ex: Exception) -> None:
        self.disconnected += 1
        self.last_exception = ex

    def controller_reconnected(self, _ctrl: Controller) -> None:
        self.reconnected += 1


def _fault_system_settings(device_uid: str) -> dict[str, object]:
    settings = deepcopy(SYSTEMS["000000001"]["SystemSettings"])
    settings["AirStreamDeviceUId"] = device_uid
    settings["NoOfZones"] = 0
    settings["SysFan"] = "error"
    settings["RAS"] = "error"
    settings["UnitType"] = "No Unit Type Configured!"
    return settings


@pytest.mark.asyncio
async def test_controller_property_reads(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

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
    controller = cast(MockController, service._controllers["000000001"])

    await controller.set_on(False)
    assert controller.is_on is False
    assert controller.sent[-1] == ("SystemON", {"SystemON": "off"})

    await controller.set_on(True)
    assert controller.is_on is True


@pytest.mark.asyncio
async def test_set_fan(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    fan = Controller.Fan.LOW

    await controller.set_fan(fan)
    assert controller.fan == fan
    assert controller.sent[-1] == ("SystemFAN", {"SystemFAN": "low"})


@pytest.mark.asyncio
async def test_set_sleep_timer(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    await controller.set_sleep_timer(30)
    assert controller.sleep_timer == 30

    await controller.set_sleep_timer(0)
    assert controller.sleep_timer == 0


@pytest.mark.asyncio
async def test_set_temp_setpoint(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    await controller.set_temp_setpoint(22.5)
    assert controller.temp_setpoint == pytest.approx(22.5)


@pytest.mark.asyncio
async def test_set_fan_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    with pytest.raises(AttributeError, match="Fan mode top not allowed"):
        await controller.set_fan(Controller.Fan.TOP)


@pytest.mark.asyncio
async def test_set_sleep_timer_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    with pytest.raises(AttributeError, match="Invalid Sleep Timer"):
        await controller.set_sleep_timer(45)


@pytest.mark.asyncio
async def test_set_temp_setpoint_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    with pytest.raises(AttributeError, match="not rounded to nearest 0.5"):
        await controller.set_temp_setpoint(23.3)
    with pytest.raises(AttributeError, match="out of range"):
        await controller.set_temp_setpoint(35.0)


@pytest.mark.asyncio
async def test_refresh_all(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    await controller._refresh_all(notify=False)

    assert controller.mode == Controller.Mode.HEAT
    assert controller.zones[0].name == "LIVING"


@pytest.mark.asyncio
async def test_initialize_succeeds_when_power_init_fails() -> None:
    svc = MockDiscoveryService()
    original_create = svc._create_controller

    def create_controller(
        device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> MockController:
        controller = original_create(device_uid, device_ip, is_v2, is_ipower)
        controller.fail_power_types.add(1)
        return controller

    svc._create_controller = create_controller

    try:
        await _register_mock_service(
            svc,
            b"ASPort_12107,Mac_000000003,IP_10.0.0.1,iZone,iPower",
        )
        controller = cast(MockController, svc._controllers["000000003"])

        assert controller.connected is True
        assert controller.power is None
        assert controller.is_ipower is False
        assert controller._initialized is True
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_initialize_sets_power_when_ipower_works(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controllers["000000003"])

    assert controller.power is not None
    assert controller.power.enabled is True
    assert controller.is_ipower is True


@pytest.mark.asyncio
async def test_initialize_does_not_fetch_power_status(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controllers["000000003"])
    power_requests = [
        data for command, data in controller.sent if command == "PowerRequest"
    ]

    assert len(power_requests) == 1
    assert power_requests[0]["PowerRequest"]["Type"] == 1

    await controller._refresh_power(notify=False)

    status_requests = [
        data
        for command, data in controller.sent
        if command == "PowerRequest" and data["PowerRequest"]["Type"] == 2
    ]
    assert len(status_requests) == 1


@pytest.mark.asyncio
async def test_initialize_clears_ipower_when_config_disabled() -> None:
    disabled_config = deepcopy(POWER_CONFIG)
    disabled_config["Enabled"] = 0

    svc = MockDiscoveryService()
    original_create = svc._create_controller

    def create_controller(
        device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> MockController:
        controller = original_create(device_uid, device_ip, is_v2, is_ipower)
        controller.power_config = disabled_config
        return controller

    svc._create_controller = create_controller

    try:
        await _register_mock_service(
            svc,
            b"ASPort_12107,Mac_000000003,IP_10.0.0.1,iZone,iPower",
        )
        controller = cast(MockController, svc._controllers["000000003"])

        assert controller.power is None
        assert controller.is_ipower is False
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_power_init_probe_failure_leaves_controller_connected() -> None:
    svc = MockDiscoveryService()
    original_create = svc._create_controller

    def create_controller(
        device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> MockController:
        controller = original_create(device_uid, device_ip, is_v2, is_ipower)
        controller.fail_power_types.add(1)
        return controller

    svc._create_controller = create_controller

    try:
        await _register_mock_service(
            svc,
            b"ASPort_12107,Mac_000000003,IP_10.0.0.1,iZone,iPower",
        )
        controller = cast(MockController, svc._controllers["000000003"])

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
    controller = cast(MockController, ipower_service._controllers["000000003"])
    assert controller.power is not None
    power_requests_before = [
        data for command, data in controller.sent if command == "PowerRequest"
    ]
    controller._bridge_ok = False

    await controller._refresh_power(notify=False)

    power_requests_after = [
        data for command, data in controller.sent if command == "PowerRequest"
    ]
    assert power_requests_after == power_requests_before


@pytest.mark.asyncio
async def test_power_poll_failure_does_not_mark_controller_disconnected(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controllers["000000003"])
    assert controller.power is not None
    controller.fail_power_types.add(2)

    await controller._refresh_power(notify=False)

    assert controller.connected is True
    assert controller.power.connected is False


@pytest.mark.asyncio
async def test_v2_probe_sets_is_v2_when_systemv2_returned() -> None:
    svc = MockDiscoveryService()
    original_create = svc._create_controller

    def create_controller(
        device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> MockController:
        controller = original_create(device_uid, device_ip, is_v2, is_ipower)
        controller.v2_probe_response = (
            '{"AirStreamDeviceUId":"000000001","SystemV2":{"SysOn":"on"}}'
        )
        return controller

    svc._create_controller = create_controller

    try:
        await _register_mock_service(
            svc,
            b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate",
        )
        controller = cast(MockController, svc._controllers["000000001"])

        assert controller.is_v2 is True
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_v2_probe_clears_is_v2_on_http_error() -> None:
    svc = MockDiscoveryService()
    try:
        await _register_mock_service(
            svc,
            b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZoneV2,iLight,iDrate",
        )
        controller = cast(MockController, svc._controllers["000000001"])

        assert controller.is_v2 is False
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_bridge_ok_true_when_izone_fault(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    healthy_fan = controller.fan

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller._refresh_system(notify=False)

    assert controller.bridge_connected is True
    assert controller.connected is False
    assert controller.fan == healthy_fan


@pytest.mark.asyncio
async def test_cache_preserved_on_izone_fault(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    healthy_settings = deepcopy(controller.resources["SystemSettings"])

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller._refresh_system(notify=False)

    assert controller._system_settings == healthy_settings


@pytest.mark.asyncio
async def test_connected_restored_on_izone_recovery(
    service: MockDiscoveryService,
) -> None:
    listener = _DisconnectListener()
    controller = cast(MockController, service._controllers["000000001"])
    controller._event_coordinator = listener
    healthy_settings = deepcopy(controller.resources["SystemSettings"])

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller._refresh_system(notify=True)
    assert listener.disconnected == 1

    controller.resources["SystemSettings"] = healthy_settings
    await controller._refresh_system(notify=True)
    assert controller.connected is True
    assert listener.reconnected == 1


@pytest.mark.asyncio
async def test_no_listener_flutter_repeated_fault_polls(
    service: MockDiscoveryService,
) -> None:
    listener = _DisconnectListener()
    controller = cast(MockController, service._controllers["000000001"])
    controller._event_coordinator = listener

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller._refresh_system(notify=True)
    await controller._refresh_system(notify=True)

    assert listener.disconnected == 1
    assert listener.reconnected == 0


@pytest.mark.asyncio
async def test_izone_fault_disconnect_uses_connection_error(
    service: MockDiscoveryService,
) -> None:
    listener = _DisconnectListener()
    controller = cast(MockController, service._controllers["000000001"])
    controller._event_coordinator = listener

    controller.resources["SystemSettings"] = _fault_system_settings("000000001")
    await controller._refresh_system(notify=True)

    assert isinstance(listener.last_exception, ConnectionError)


@pytest.mark.asyncio
async def test_v2_probe_failure_leaves_bridge_ok(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    controller.v2_probe_response = None

    await controller._probe_v2_api()

    assert controller._bridge_ok is True
    assert controller.bridge_connected is True


@pytest.mark.asyncio
async def test_init_fault_disconnect_listener() -> None:
    listener = _DisconnectListener()
    svc = MockDiscoveryService()
    controller = MockController.from_discovery(
        svc,
        listener,
        device_uid="000000004",
        device_ip="10.0.0.4",
        is_v2=False,
        is_ipower=False,
    )
    controller.resources["SystemSettings"] = _fault_system_settings("000000004")

    await controller._initialize()

    assert controller.connected is False
    assert listener.disconnected == 1
    assert controller.zones == []


@pytest.mark.asyncio
async def test_init_fault_property_reads_no_crash() -> None:
    listener = _DisconnectListener()
    svc = MockDiscoveryService()
    controller = MockController.from_discovery(
        svc,
        listener,
        device_uid="000000005",
        device_ip="10.0.0.5",
        is_v2=False,
        is_ipower=False,
    )
    controller.resources["SystemSettings"] = _fault_system_settings("000000005")

    await controller._initialize()

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
    controller = cast(MockController, service._controllers["000000001"])
    controller._system_settings["SysFan"] = "error"
    controller._system_settings["RAS"] = "error"
    controller._system_settings["SysMode"] = "error"

    assert controller.fan == Controller.Fan.AUTO
    assert controller.ras_mode == "zones"
    assert controller.mode == Controller.Mode.COOL


@pytest.mark.asyncio
async def test_both_false_on_transport_failure(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    controller._connected = False

    with pytest.raises(ConnectionError):
        await controller._get_resource("SystemSettings")

    assert controller.bridge_connected is False
    assert controller.connected is False
