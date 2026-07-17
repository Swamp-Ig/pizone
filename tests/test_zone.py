"""Tests for zone property reads and command validation."""

# disposition: 1.4 | deprecate  (untagged = keep)
#   keep      — default; no tag required. Shared dual-track / pathway-agnostic tests.
#   1.4       — new consumer-driven discovery / refresh API
#   deprecate — legacy track; grep and delete when dual-track ends
#               (sticky within a function until the next disposition tag).

from typing import cast

import pytest

from pizone import Zone

from .conftest import MockController, MockDiscoveryService

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
