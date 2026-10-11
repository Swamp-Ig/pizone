"""Tests for zone property reads and command validation."""

# disposition: 1.4 | deprecate  (untagged = keep)
#   keep      — default; no tag required. Shared dual-track / pathway-agnostic tests.
#   1.4       — new consumer-driven discovery / refresh API
#   deprecate — legacy track; grep and delete when dual-track ends
#               (sticky within a function until the next disposition tag).

from collections.abc import AsyncIterator
from copy import deepcopy
from typing import cast
from unittest.mock import AsyncMock

import pytest

from pizone import Zone, v2 as v2_mod
from pizone.controller import ControllerCommandError

from .conftest import MockController, MockDiscoveryService
from .v2_fixtures import SAMPLE_SYSTEM_V2, SAMPLE_ZONES_V2


@pytest.fixture
async def v2_controller() -> AsyncIterator[MockController]:
    svc = MockDiscoveryService(legacy_pathway=False)
    controller = MockController.from_discovery(
        svc,
        svc._event_coordinator,
        device_uid="000025841",
        device_ip="192.0.2.1",
        is_v2=True,
        is_ipower=False,
    )
    controller._read_api = "v2"
    controller._v2_use_content_type = False
    controller.v2_system = deepcopy(SAMPLE_SYSTEM_V2)
    controller.v2_zones = deepcopy(SAMPLE_ZONES_V2)
    svc._controllers[controller.device_uid] = controller
    settings = v2_mod.system_v2_to_settings(
        SAMPLE_SYSTEM_V2["SystemV2"], controller.device_uid
    )
    try:
        await controller._initialize(system_settings=settings)
        controller.sent.clear()
        yield controller
    finally:
        await svc.close()


# disposition: 1.4
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "wire_mode"),
    [(Zone.Mode.OPEN, 1), (Zone.Mode.CLOSE, 2), (Zone.Mode.AUTO, 3)],
)
async def test_v2_mode_selection_does_not_write_stale_setpoint(
    v2_controller: MockController, mode: Zone.Mode, wire_mode: int
) -> None:
    zone = v2_controller.zones[0]
    assert zone.temp_setpoint == 22.5
    # A wall-panel change has not reached the cached zone yet.
    v2_controller.v2_zones[0]["ZonesV2"]["Setpoint"] = 2350
    v2_controller.v2_zones[0]["ZonesV2"]["Mode"] = wire_mode

    await zone.set_mode(mode)
    # Test the payload and resulting readback, not whether confirmation blocks.
    await v2_controller.refresh_zones()

    assert [item for item in v2_controller.sent if item[0] == "iZoneCommandV2"] == [
        ("iZoneCommandV2", {"ZoneMode": {"Index": 0, "Mode": wire_mode}}),
    ]
    assert zone.mode == mode
    assert zone.temp_setpoint == 23.5


# disposition: 1.4
@pytest.mark.asyncio
async def test_v2_auto_mode_does_not_need_cached_setpoint(
    v2_controller: MockController,
) -> None:
    zone = v2_controller.zones[0]
    del zone._zone_data["SetPoint"]

    await zone.set_mode(Zone.Mode.AUTO)
    await v2_controller.refresh_zones()

    assert v2_controller.sent[0] == (
        "iZoneCommandV2",
        {"ZoneMode": {"Index": 0, "Mode": 3}},
    )
    assert zone.temp_setpoint == 22.5


# disposition: 1.4
@pytest.mark.asyncio
async def test_v2_auto_on_open_close_zone_does_not_send(
    v2_controller: MockController,
) -> None:
    zone = v2_controller.zones[1]
    before = zone.dump_state()
    with pytest.raises(AttributeError, match="Can't use auto mode on open/close zone"):
        await zone.set_mode(Zone.Mode.AUTO)
    assert v2_controller.sent == []
    assert zone.dump_state() == before


# disposition: 1.4
@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError, ControllerCommandError])
async def test_v2_failed_auto_command_does_not_publish_mode(
    v2_controller: MockController, error: type[Exception]
) -> None:
    zone = v2_controller.zones[0]
    zone._zone_data["Mode"] = "close"
    before = zone.dump_state()
    send = AsyncMock(side_effect=error("command rejected"))
    v2_controller._http_command_v2 = send  # type: ignore[method-assign]

    with pytest.raises(error, match="command rejected"):
        await zone.set_mode(Zone.Mode.AUTO)

    send.assert_awaited_once_with({"ZoneMode": {"Index": 0, "Mode": 3}})
    assert zone.dump_state() == before
    assert v2_controller.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_pathway", [True, False])
@pytest.mark.parametrize("is_v2", [True, False])
async def test_v1_auto_still_sends_cached_setpoint(
    legacy_pathway: bool, is_v2: bool
) -> None:
    svc = MockDiscoveryService(legacy_pathway=legacy_pathway)
    controller = MockController.from_discovery(
        svc,
        svc._event_coordinator,
        device_uid="000000001",
        device_ip="192.0.2.1",
        is_v2=is_v2,
        is_ipower=False,
    )
    svc._controllers[controller.device_uid] = controller
    try:
        await controller._initialize(
            system_settings=deepcopy(controller.resources["SystemSettings"])
        )
        controller._is_v2 = is_v2
        assert controller._read_api == "v1"
        controller.sent.clear()

        await controller.zones[1].set_mode(Zone.Mode.AUTO)

        assert controller.sent == [
            ("ZoneCommand", {"ZoneCommand": {"ZoneNo": "2", "Command": "23.5"}})
        ]
        assert controller.zones[1].mode == Zone.Mode.AUTO
    finally:
        await svc.close()


# disposition: deprecate
@pytest.mark.asyncio
async def test_zone_property_reads(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]

    assert zone.index == 1
    assert zone.name == "LOUNGE"
    assert zone.type == Zone.Type.AUTO
    assert zone.mode == Zone.Mode.AUTO
    assert zone.temp_setpoint == 23.5
    assert zone.temp_current == pytest.approx(23.58)
    assert zone.airflow_min == 0
    assert zone.airflow_max == 90


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_airflow_min(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]

    await zone.set_airflow_min(20)

    assert controller.sent[-1] == (
        "AirMinCommand",
        {"AirMinCommand": {"ZoneNo": "2", "Command": "20"}},
    )
    assert zone.airflow_min == 20


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_airflow_max(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]

    await zone.set_airflow_max(80)

    assert controller.sent[-1] == (
        "AirMaxCommand",
        {"AirMaxCommand": {"ZoneNo": "2", "Command": "80"}},
    )
    assert zone.airflow_max == 80


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_temp_setpoint(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]

    await zone.set_temp_setpoint(22.0)

    assert controller.sent[-1] == (
        "ZoneCommand",
        {"ZoneCommand": {"ZoneNo": "2", "Command": "22.0"}},
    )
    assert zone.mode == Zone.Mode.AUTO
    assert zone.temp_setpoint == 22.0


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_mode_open_and_close(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]

    await zone.set_mode(Zone.Mode.OPEN)
    assert zone.mode == Zone.Mode.OPEN
    assert controller.sent[-1] == (
        "ZoneCommand",
        {"ZoneCommand": {"ZoneNo": "2", "Command": "open"}},
    )

    await zone.set_mode(Zone.Mode.CLOSE)
    assert zone.mode == Zone.Mode.CLOSE


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_airflow_min_validation(service: MockDiscoveryService) -> None:
    zone = cast(MockController, service._controllers["000000001"]).zones[1]

    with pytest.raises(AttributeError, match="not rounded to nearest 5"):
        await zone.set_airflow_min(41)
    with pytest.raises(AttributeError, match="out of range"):
        await zone.set_airflow_min(110)


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_airflow_max_validation(service: MockDiscoveryService) -> None:
    zone = cast(MockController, service._controllers["000000001"]).zones[1]

    with pytest.raises(AttributeError, match="not rounded to nearest 5"):
        await zone.set_airflow_max(41)
    with pytest.raises(AttributeError, match="out of range"):
        await zone.set_airflow_max(110)


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_temp_setpoint_validation(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]
    opcl = controller.zones[2]
    opcl._zone_data["Type"] = "opcl"

    with pytest.raises(AttributeError, match="Can't set SetPoint"):
        await opcl.set_temp_setpoint(22.0)
    with pytest.raises(AttributeError, match="not rounded to nearest 0.5"):
        await zone.set_temp_setpoint(22.3)
    with pytest.raises(AttributeError, match="out of range"):
        await zone.set_temp_setpoint(35.0)


# disposition: deprecate
@pytest.mark.asyncio
async def test_set_mode_auto_on_opcl_zone(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[2]
    zone._zone_data["Type"] = "opcl"

    with pytest.raises(AttributeError, match="Can't use auto mode on open/close zone"):
        await zone.set_mode(Zone.Mode.AUTO)


# disposition: deprecate
@pytest.mark.asyncio
async def test_update_zone_index_mismatch(service: MockDiscoveryService) -> None:
    zone = cast(MockController, service._controllers["000000001"]).zones[1]
    bad_data = dict(zone._zone_data)
    bad_data["Index"] = 99

    with pytest.raises(AttributeError, match="Can't change index"):
        zone._update_zone(bad_data, notify=False)
