"""Mappings verified against the official iZone API header v1.41."""

from typing import cast

import pytest

from pizone import v2

from .conftest import MockController, MockDiscoveryService
from .v2_fixtures import SAMPLE_SYSTEM_V2


@pytest.mark.parametrize(("raw", "expected"), [(1, "RAS"), (2, "master"), (3, "zones")])
def test_documented_return_air_sensor(raw: int, expected: str) -> None:
    system = {**SAMPLE_SYSTEM_V2["SystemV2"], "RAS": raw}
    assert v2.system_v2_to_settings(system, "000025841")["RAS"] == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [(0, "2-speed"), (1, "3-speed"), (2, "var-speed"), (3, "4-speed"), (4, "unknown")],
)
@pytest.mark.parametrize("enabled", [0, 1])
def test_documented_fan_capability(kind: int, expected: str, enabled: int) -> None:
    system = {**SAMPLE_SYSTEM_V2["SystemV2"], "FanAutoEn": enabled, "FanAutoType": kind}
    assert v2.system_v2_to_settings(system, "000025841")["FanAuto"] == (
        expected if enabled else "disabled"
    )


@pytest.mark.parametrize("missing", [False, True])
def test_missing_fan_type_is_not_two_speed(missing: bool) -> None:
    system = {**SAMPLE_SYSTEM_V2["SystemV2"], "FanAutoEn": 1, "FanAutoType": None}
    if missing:
        del system["FanAutoType"]
    assert v2.system_v2_to_settings(system, "000025841")["FanAuto"] == "unknown"


@pytest.mark.parametrize(("raw", "unit_owns"), [(1, True), (2, False), (3, False)])
@pytest.mark.asyncio
async def test_wire_ras_selects_correct_setpoint_owner(
    service: MockDiscoveryService, raw: int, unit_owns: bool
) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    system = {**SAMPLE_SYSTEM_V2["SystemV2"], "RAS": raw, "CtrlZone": 1}
    normalized = v2.system_v2_to_settings(system, controller.device_uid)
    controller._system_settings.update(
        RAS=normalized["RAS"], CtrlZone=normalized["CtrlZone"]
    )
    expected = controller if unit_owns else controller.zones[1]
    assert controller.control_setpoint_owner is expected
