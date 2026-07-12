"""Tests for controller property reads and command paths."""

# pylint: disable=protected-access
from typing import cast

import pytest
from pytest import raises

from pizone import Controller

from .conftest import MockController, MockDiscoveryService


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
