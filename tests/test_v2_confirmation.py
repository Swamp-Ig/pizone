"""Network-free V2 mode confirmation and command/poll ordering regressions."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from pizone import Zone, v2
from pizone.exceptions import ControllerCommandError

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
    ctrl.ZONE_CONFIRM_DELAY = 0.05
    ctrl.ZONE_CONFIRM_INTERVAL = 0.05
    ctrl.ZONE_CONFIRM_WINDOW = 0.35
    ctrl.ZONE_OPTIMISTIC_WINDOW = 0.1
    svc._controllers[ctrl.device_uid] = ctrl
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


async def settled(ctrl: MockController) -> None:
    if ctrl._zone_confirm_task is not None:
        await asyncio.wait_for(ctrl._zone_confirm_task, 2)


def writes(ctrl: MockController) -> list:
    return [item for item in ctrl.sent if item[0] == "iZoneCommandV2"]


@pytest.mark.asyncio
async def test_ack_returns_before_confirmation_read(controller: MockController) -> None:
    controller.v2_zones[0]["ZonesV2"]["Mode"] = 2
    read = AsyncMock(wraps=controller._v2_request)
    controller._v2_request = read
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    read.assert_not_awaited()
    assert controller.zones[0].mode == Zone.Mode.CLOSE
    await settled(controller)
    read.assert_awaited_once_with(2, 0)
    assert not controller._zone_mode_confirmations
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
async def test_delayed_mode_tracks_after_optimistic_window(
    controller: MockController,
) -> None:
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    pending = controller._zone_mode_confirmations[0]
    # First fresh mismatch is masked briefly, but is not treated as confirmation.
    await controller.refresh_zones()
    assert controller.zones[0].mode == Zone.Mode.CLOSE
    assert controller._zone_mode_confirmations[0] is pending
    pending.issued -= controller.ZONE_OPTIMISTIC_WINDOW
    await controller.refresh_zones()
    assert controller.zones[0].mode == Zone.Mode.AUTO
    assert controller._zone_mode_confirmations[0] is pending
    controller.v2_zones[0]["ZonesV2"]["Mode"] = 2
    await settled(controller)
    assert controller.zones[0].mode == Zone.Mode.CLOSE
    assert not controller._zone_mode_confirmations
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
async def test_persistent_mismatch_expires_without_outage(
    controller: MockController,
) -> None:
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await settled(controller)
    assert not controller._zone_mode_confirmations
    assert controller.bridge_connected
    assert controller.zones[0].mode == Zone.Mode.AUTO
    reads = [item for item in controller.sent if item[0] == "iZoneRequestV2"]
    assert 1 <= len(reads) <= 7
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
async def test_confirmation_deadline_cancels_read_without_outage(
    controller: MockController,
) -> None:
    entered = asyncio.Event()

    async def hold(_type, _index):
        entered.set()
        await asyncio.Event().wait()

    controller._v2_request = hold
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await asyncio.wait_for(entered.wait(), 1)
    await settled(controller)
    assert controller.bridge_connected
    assert not controller._zone_mode_confirmations
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
async def test_successful_read_crossing_deadline_stays_healthy(
    controller: MockController,
) -> None:
    original = controller._v2_request

    async def finish_late(kind, index):
        result = await original(kind, index)
        # Synchronous expiry models a response arriving at the deadline boundary.
        controller._zone_mode_confirmations[index].deadline = (
            asyncio.get_running_loop().time() - 1
        )
        return result

    controller._v2_request = finish_late
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await settled(controller)
    assert controller.bridge_connected
    assert not controller._zone_mode_confirmations
    assert len(writes(controller)) == 1
    assert len([x for x in controller.sent if x[0] == "iZoneRequestV2"]) == 1


@pytest.mark.asyncio
async def test_rapid_selections_coalesce_reads_not_writes(
    controller: MockController,
) -> None:
    controller.v2_zones[0]["ZonesV2"]["Mode"] = 2
    for mode in (Zone.Mode.CLOSE, Zone.Mode.OPEN, Zone.Mode.CLOSE):
        await controller.zones[0].set_mode(mode)
    await settled(controller)
    assert [item[1]["ZoneMode"]["Mode"] for item in writes(controller)] == [2, 1, 2]
    assert len([x for x in controller.sent if x[0] == "iZoneRequestV2"]) == 1
    assert controller.zones[0].mode == Zone.Mode.CLOSE


@pytest.mark.asyncio
async def test_close_cancels_confirmation_work(controller: MockController) -> None:
    entered = asyncio.Event()

    async def hold(_type, _index):
        entered.set()
        await asyncio.Event().wait()

    controller._v2_request = hold
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    task = controller._zone_confirm_task
    await asyncio.wait_for(entered.wait(), 1)
    await controller.close()
    assert task.done()
    assert controller._zone_confirm_task is None
    assert not controller._zone_mode_confirmations
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError, ControllerCommandError])
async def test_failed_write_does_not_schedule_confirmation(
    controller: MockController,
    error: type[Exception],
) -> None:
    controller._http_command_v2 = AsyncMock(side_effect=error("offline"))
    before = controller.zones[0].dump_state()
    with pytest.raises(error, match="offline"):
        await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    assert controller._zone_confirm_task is None
    assert not controller._zone_mode_confirmations
    assert controller.zones[0].dump_state() == before


@pytest.mark.asyncio
async def test_transport_error_is_not_hidden_as_mode_mismatch(
    controller: MockController,
) -> None:
    controller._v2_request = AsyncMock(side_effect=ConnectionError("offline"))
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await settled(controller)
    assert not controller.bridge_connected
    assert not controller._zone_mode_confirmations
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
async def test_command_runs_between_polling_zone_reads(
    controller: MockController,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    order = []
    original_read, original_write = controller._v2_request, controller._http_command_v2

    async def read(kind, index):
        order.append(f"read{index}")
        if index == 0:
            entered.set()
            await release.wait()
        return await original_read(kind, index)

    async def write(payload):
        order.append("write")
        return await original_write(payload)

    controller._v2_request, controller._http_command_v2 = read, write
    poll = asyncio.create_task(controller.refresh_zones())
    await asyncio.wait_for(entered.wait(), 1)
    command = asyncio.create_task(controller.zones[1].set_mode(Zone.Mode.CLOSE))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(poll, command), 2)
    assert order[:3] == ["read0", "write", "read1"]
    await settled(controller)
    assert len(writes(controller)) == 1


@pytest.mark.asyncio
async def test_new_selection_survives_an_older_inflight_read(
    controller: MockController,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = controller._v2_request
    calls = 0

    async def read(kind, index):
        nonlocal calls
        calls += 1
        snapshot = await original(kind, index)
        if calls == 1:
            entered.set()
            await release.wait()
        return snapshot

    controller.v2_zones[0]["ZonesV2"]["Mode"] = 2
    controller._v2_request = read
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await asyncio.wait_for(entered.wait(), 1)
    newer = asyncio.create_task(controller.zones[0].set_mode(Zone.Mode.OPEN))
    await asyncio.sleep(0)
    controller.v2_zones[0]["ZonesV2"]["Mode"] = 1
    release.set()
    await asyncio.wait_for(newer, 1)
    await settled(controller)
    assert controller.zones[0].mode == Zone.Mode.OPEN
    assert not controller._zone_mode_confirmations
    assert [item[1]["ZoneMode"]["Mode"] for item in writes(controller)] == [2, 1]


@pytest.mark.asyncio
async def test_expiry_while_waiting_for_lock_does_not_read(
    controller: MockController,
) -> None:
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    async with controller._sending_lock:
        await settled(controller)
    assert not [item for item in controller.sent if item[0] == "iZoneRequestV2"]
    assert controller.bridge_connected
    assert not controller._zone_mode_confirmations


@pytest.mark.asyncio
async def test_discovery_shutdown_clears_pending_work(
    controller: MockController,
) -> None:
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await asyncio.sleep(0)
    await controller.discovery.close()
    assert controller._zone_confirm_task is None
    assert not controller._zone_mode_confirmations


@pytest.mark.asyncio
async def test_two_zones_share_confirmation_rate_limit(
    controller: MockController,
) -> None:
    times = []
    original = controller._v2_request

    async def read(kind, index):
        times.append(asyncio.get_running_loop().time())
        return await original(kind, index)

    controller._v2_request = read
    controller.v2_zones[0]["ZonesV2"]["Mode"] = 2
    await controller.zones[0].set_mode(Zone.Mode.CLOSE)
    await controller.zones[1].set_mode(Zone.Mode.CLOSE)
    await settled(controller)
    assert len(times) == 2
    assert times[1] - times[0] >= controller.ZONE_CONFIRM_INTERVAL
    assert len(writes(controller)) == 2
