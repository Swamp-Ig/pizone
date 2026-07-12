"""Tests for controller property reads and command paths."""

# pylint: disable=protected-access
from copy import deepcopy
from typing import cast

import pytest
from pytest import raises

from pizone import Controller

from .conftest import MockController, MockDiscoveryService, _register_mock_service
from .power_data import POWER_CONFIG


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

    with raises(AttributeError, match="Fan mode top not allowed"):
        await controller.set_fan(Controller.Fan.TOP)


@pytest.mark.asyncio
async def test_set_sleep_timer_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    with raises(AttributeError, match="Invalid Sleep Timer"):
        await controller.set_sleep_timer(45)


@pytest.mark.asyncio
async def test_set_temp_setpoint_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    with raises(AttributeError, match="not rounded to nearest 0.5"):
        await controller.set_temp_setpoint(23.3)
    with raises(AttributeError, match="out of range"):
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
        data
        for command, data in controller.sent
        if command == "PowerRequest"
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
async def test_power_init_probe_timeout_does_not_mark_disconnected() -> None:
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
        assert controller._fail_exception is None
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_power_poll_timeout_marks_disconnected(
    ipower_service: MockDiscoveryService,
) -> None:
    controller = cast(MockController, ipower_service._controllers["000000003"])
    controller.fail_power_types.add(2)

    with raises(ConnectionError):
        await controller._refresh_power(notify=False)

    assert controller.connected is False


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
