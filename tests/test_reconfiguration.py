"""Latching on a device reconfiguration seen in a clean read."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from pizone import DeviceReconfigurationError, v2

from .conftest import MockController, MockDiscoveryService, add_mock_controller
from .v2_fixtures import SAMPLE_SYSTEM_V2, SAMPLE_ZONES_V2


@pytest.fixture
async def v2_controller() -> AsyncIterator[MockController]:
    svc = MockDiscoveryService()
    ctrl = MockController(
        svc,
        device_uid="000025841",
        device_ip="192.0.2.1",
        is_v2=True,
        is_ipower=False,
    )
    ctrl._read_api = "v2"
    ctrl.v2_system = deepcopy(SAMPLE_SYSTEM_V2)
    ctrl.v2_zones = deepcopy(SAMPLE_ZONES_V2)
    svc._claim(ctrl)
    try:
        await ctrl._initialize(
            system_settings=v2.system_v2_to_settings(
                SAMPLE_SYSTEM_V2["SystemV2"], ctrl.device_uid
            )
        )
        ctrl.sent.clear()
        yield ctrl
    finally:
        await ctrl.close()
        await svc.close()


def _v1(service: MockDiscoveryService) -> MockController:
    return cast(MockController, service._controller("000000001"))


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        pytest.param("NoOfZones", 7, (8, 7), id="zone_count"),
        pytest.param("NoOfConst", 2, (1, 2), id="const_count"),
        pytest.param("SysType", "310", ("320", "310"), id="sys_type"),
        pytest.param("FanAuto", "disabled", ("3-speed", "disabled"), id="fan_auto"),
    ],
)
@pytest.mark.asyncio
async def test_vital_field_change_latches(
    service: MockDiscoveryService, field: str, value: Any, expected: tuple
) -> None:
    controller = _v1(service)
    controller.resources["SystemSettings"][field] = value

    with pytest.raises(DeviceReconfigurationError) as err:
        await controller.refresh_system()

    assert err.value.changes == {field: expected}
    assert controller.dump_state()["reconfigured"] == {field: expected}


@pytest.mark.asyncio
async def test_missing_fan_auto_matches_disabled() -> None:
    svc = MockDiscoveryService()
    controller = MockController(
        svc,
        device_uid="000000001",
        device_ip="8.8.8.8",
        is_v2=False,
        is_ipower=False,
    )
    del controller.resources["SystemSettings"]["FanAuto"]
    svc._claim(controller)
    await controller._initialize(
        system_settings=deepcopy(controller.resources["SystemSettings"])
    )
    # Fan modes read an absent FanAuto as disabled, so the check must too.
    controller.resources["SystemSettings"]["FanAuto"] = "disabled"

    await controller.refresh_system()

    assert controller._reconfigured is None
    await svc.close()


@pytest.mark.asyncio
async def test_faulted_read_does_not_latch(service: MockDiscoveryService) -> None:
    controller = _v1(service)
    settings = controller.resources["SystemSettings"]
    settings["NoOfConst"] = 3
    settings["UnitType"] = "error"

    await controller.refresh_system()

    assert controller._reconfigured is None
    settings["UnitType"] = "Panasonic/Toshiba"
    with pytest.raises(DeviceReconfigurationError):
        await controller.refresh_system()


@pytest.mark.asyncio
async def test_unchanged_refresh_does_not_latch(service: MockDiscoveryService) -> None:
    controller = _v1(service)

    await controller.refresh_all()
    await controller.refresh_all()

    assert controller._reconfigured is None


@pytest.mark.parametrize(
    ("zone_type", "latched"),
    [
        pytest.param("opcl", True, id="type_changed"),
        pytest.param("error", False, id="type_faulted"),
    ],
)
@pytest.mark.asyncio
async def test_zone_type_change_latches(
    service: MockDiscoveryService, zone_type: str, latched: bool
) -> None:
    controller = _v1(service)
    controller.resources["Zones1_4"][2]["Type"] = zone_type

    if latched:
        with pytest.raises(DeviceReconfigurationError) as err:
            await controller.refresh_zones()
        assert err.value.changes == {"Zone 3 Type": ("auto", "opcl")}
    else:
        await controller.refresh_zones()
        assert controller._reconfigured is None


@pytest.mark.asyncio
async def test_latched_controller_raises_before_io(
    service: MockDiscoveryService,
) -> None:
    controller = _v1(service)
    controller.resources["SystemSettings"]["NoOfZones"] = 7
    with pytest.raises(DeviceReconfigurationError):
        await controller.refresh_system()
    get = AsyncMock(wraps=controller._http_get)
    controller._http_get = get  # type: ignore[method-assign]
    controller.sent.clear()

    with pytest.raises(DeviceReconfigurationError):
        await controller.refresh_all()
    with pytest.raises(DeviceReconfigurationError):
        await controller.set_on(False)
    with pytest.raises(DeviceReconfigurationError):
        await controller.zones[0].set_temp_setpoint(22.0)

    get.assert_not_awaited()
    assert controller.sent == []


@pytest.mark.asyncio
async def test_v1_reconfigure_outranks_zone_error(
    service: MockDiscoveryService,
) -> None:
    controller = _v1(service)
    controller.resources["SystemSettings"]["NoOfZones"] = 4
    del controller.resources["Zones5_8"]

    with pytest.raises(DeviceReconfigurationError) as err:
        await controller.refresh_all()

    assert err.value.changes == {"NoOfZones": (8, 4)}
    assert isinstance(err.value.__cause__, ConnectionError)


@pytest.mark.asyncio
async def test_v2_reconfigure_raises_before_zone_requests(
    v2_controller: MockController,
) -> None:
    cast(dict, v2_controller.v2_system)["SystemV2"]["NoOfZones"] = 1

    with pytest.raises(DeviceReconfigurationError) as err:
        await v2_controller.refresh_all()

    assert err.value.changes == {"NoOfZones": (2, 1)}
    assert [item[1]["iZoneV2Request"]["Type"] for item in v2_controller.sent] == [1]


@pytest.mark.asyncio
async def test_v2_zone_type_change_latches(v2_controller: MockController) -> None:
    v2_controller.v2_zones[1]["ZonesV2"]["ZoneType"] = 3

    with pytest.raises(DeviceReconfigurationError) as err:
        await v2_controller.refresh_zones()

    assert err.value.changes == {"Zone 2 Type": ("opcl", "auto")}


@pytest.mark.asyncio
async def test_command_whose_confirm_read_latches_still_returns(
    service: MockDiscoveryService,
) -> None:
    controller = _v1(service)
    controller.resources["SystemSettings"]["NoOfZones"] = 7

    await controller.set_on(False)

    assert controller._reconfigured == {"NoOfZones": (8, 7)}
    with pytest.raises(DeviceReconfigurationError):
        await controller.set_on(True)


@pytest.mark.asyncio
async def test_confirm_loop_stops_when_latched(v2_controller: MockController) -> None:
    v2_controller.ZONE_CONFIRM_DELAY = 0.01
    v2_controller.ZONE_CONFIRM_INTERVAL = 0.01
    await v2_controller.zones[0].set_mode(v2_controller.zones[0].Mode.CLOSE)
    v2_controller._reconfigured = {"NoOfZones": (2, 1)}
    read = AsyncMock(wraps=v2_controller._v2_request)
    v2_controller._v2_request = read  # type: ignore[method-assign]
    v2_controller._zone_confirm_wake.set()

    task = v2_controller._zone_confirm_task
    assert task is not None
    await asyncio.wait_for(task, 2)

    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_recreated_controller_is_not_latched() -> None:
    svc = MockDiscoveryService()
    controller = await add_mock_controller(svc, "000000001", "8.8.8.8")
    controller.resources["SystemSettings"]["NoOfZones"] = 4
    with pytest.raises(DeviceReconfigurationError):
        await controller.refresh_system()

    await controller.close()
    fresh = MockController(
        svc,
        device_uid="000000001",
        device_ip="8.8.8.8",
        is_v2=False,
        is_ipower=False,
    )
    fresh.resources["SystemSettings"]["NoOfZones"] = 4
    svc._claim(fresh)
    await fresh._initialize(system_settings=fresh.resources["SystemSettings"])

    await fresh.refresh_all()

    assert fresh._reconfigured is None
    assert len(fresh.zones) == 4
    await svc.close()
