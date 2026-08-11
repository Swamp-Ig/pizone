"""Tests for controller control-zone setpoint ownership."""

from typing import cast

import pytest

from pizone import Zone

from .conftest import MockController, MockDiscoveryService


@pytest.mark.asyncio
async def test_zones_mode_uses_ctrlzone_despite_const_temp_zero(
    service: MockDiscoveryService,
) -> None:
    """CONST/OPCL Temp:0 must not steal control from AUTO zones."""
    controller = cast(MockController, service._controllers["000000001"])
    spill = controller.zones[7]

    assert spill.type is Zone.Type.CONST
    assert spill.temp_current is None
    assert controller.ras_mode == "zones"
    assert controller.zone_ctrl == 1
    assert controller.control_setpoint_owner is controller.zones[1]
    assert controller.zones[1].name == "LOUNGE"
    assert controller.control_setpoint == pytest.approx(23.5)


@pytest.mark.parametrize(
    ("ras_mode", "zone_ctrl", "clear_auto_temp", "owner", "setpoint"),
    [
        pytest.param("RAS", 1, False, "self", 23.5, id="ras"),
        pytest.param("master", 13, False, "self", 23.5, id="master_unit"),
        pytest.param("master", 1, False, 1, 23.5, id="master_zone"),
        pytest.param("zones", 1, True, "self", 23.5, id="auto_missing_cts"),
        pytest.param("zones", 99, False, None, None, id="unmatched_zone"),
        pytest.param("zones", 7, False, 7, None, id="const_ctrlzone"),
    ],
)
@pytest.mark.asyncio
async def test_control_zone_ownership(
    service: MockDiscoveryService,
    ras_mode: str,
    zone_ctrl: int,
    clear_auto_temp: bool,
    owner: int | str | None,
    setpoint: float | None,
) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    controller._system_settings["RAS"] = ras_mode
    controller._system_settings["CtrlZone"] = zone_ctrl
    if clear_auto_temp:
        for zone in controller.zones:
            if zone.type is Zone.Type.AUTO:
                zone._zone_data["Temp"] = 0

    if owner is None:
        assert controller.control_setpoint_owner is None
    elif owner == "self":
        assert controller.control_setpoint_owner is controller
    else:
        assert controller.control_setpoint_owner is controller.zones[owner]
    if setpoint is None:
        assert controller.control_setpoint is None
    else:
        assert controller.control_setpoint == pytest.approx(setpoint)
