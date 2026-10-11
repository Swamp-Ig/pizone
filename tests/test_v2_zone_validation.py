"""Reject bad V2 snapshots without replacing known zone state."""

from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

import pytest

from pizone import v2

from .conftest import MockController, MockDiscoveryService
from .v2_fixtures import SAMPLE_SYSTEM_V2, SAMPLE_ZONES_V2


@pytest.fixture
async def controller() -> AsyncIterator[MockController]:
    svc = MockDiscoveryService(legacy_pathway=False)
    ctrl = MockController.from_discovery(
        svc,
        svc._event_coordinator,
        device_uid="000025841",
        device_ip="192.0.2.1",
        is_v2=True,
        is_ipower=False,
    )
    ctrl._read_api = "v2"
    ctrl.v2_system = deepcopy(SAMPLE_SYSTEM_V2)
    ctrl.v2_zones = deepcopy(SAMPLE_ZONES_V2)
    svc._controllers[ctrl.device_uid] = ctrl
    try:
        await ctrl._initialize(
            system_settings=v2.system_v2_to_settings(
                SAMPLE_SYSTEM_V2["SystemV2"], ctrl.device_uid
            )
        )
        yield ctrl
    finally:
        await svc.close()


@pytest.mark.parametrize("value", [0, 5, 100])
def test_max_air_preserves_reported_value(value: int) -> None:
    zone = {**SAMPLE_ZONES_V2[0]["ZonesV2"], "MaxAir": value}
    assert v2.zones_v2_to_zone_data(zone)["MaxAir"] == value


@pytest.mark.parametrize(
    "field",
    ["Index", "Name", "ZoneType", "Mode", "Setpoint", "Temp", "MaxAir", "MinAir"],
)
def test_incomplete_zone_not_useful(field: str) -> None:
    data = deepcopy(SAMPLE_ZONES_V2[0])
    del data["ZonesV2"][field]
    assert not v2.zones_v2_useful(data, index=0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Index", None),
        ("Index", "bad"),
        ("Index", -1),
        ("Index", True),
        ("Index", 0.5),
        ("Name", {}),
        ("Mode", None),
        ("Mode", "bad"),
        ("ZoneType", {}),
        ("Temp", float("nan")),
        ("Setpoint", float("inf")),
        ("Temp", True),
        ("MaxAir", -1),
        ("MaxAir", 101),
        ("MinAir", None),
        ("ConstA", {}),
    ],
)
def test_malformed_zone_not_useful(field: str, value: Any) -> None:
    data = deepcopy(SAMPLE_ZONES_V2[0])
    data["ZonesV2"][field] = value
    assert not v2.zones_v2_useful(data, index=0)


@pytest.mark.parametrize("uid", [None, "", "000000999", 25841])
def test_response_must_match_expected_hub(uid: Any) -> None:
    data = {**SAMPLE_ZONES_V2[0], "AirStreamDeviceUId": uid}
    assert not v2.zones_v2_useful(data, index=0, uid="000025841")


def test_matching_hub_is_useful() -> None:
    assert v2.zones_v2_useful(SAMPLE_ZONES_V2[0], index=0, uid="000025841")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wrong_hub", "missing_mode", "bad_index"])
@pytest.mark.parametrize("single", [False, True])
async def test_rejected_zone_keeps_previous_cache(
    controller: MockController, kind: str, single: bool
) -> None:
    before = controller.zones[0].dump_state()
    incoming = controller.v2_zones[0]
    if kind == "wrong_hub":
        incoming["AirStreamDeviceUId"] = "000000999"
    elif kind == "missing_mode":
        del incoming["ZonesV2"]["Mode"]
    else:
        incoming["ZonesV2"]["Index"] = "bad"

    if single:
        async with controller._sending_lock:
            with pytest.raises(ConnectionError, match="zone"):
                await controller._fetch_zone_v2_unlocked(0)
    else:
        with pytest.raises(ConnectionError, match="zone"):
            await controller.refresh_zones()
    assert controller.zones[0].dump_state() == before


@pytest.mark.asyncio
async def test_zero_airflow_survives_controller_refresh(
    controller: MockController,
) -> None:
    controller.v2_zones[0]["ZonesV2"]["MaxAir"] = 0
    await controller.refresh_zones()
    assert controller.zones[0].airflow_max == 0
