"""Hardware tests for a live iZone controller.

Covers what offline fakes can't: parsing the bridge's real payloads, and writes
the firmware has to accept and report back. Value validation happens in the
library before any I/O and is covered by the unit tests.

Write tests change the live system, then restore it. Bridges can take several
seconds to report a write, so read-backs poll until the value shows up.
"""

import asyncio
from collections.abc import Callable
import json

import pytest

from pizone import Controller, Zone

# How long a write may take to show up on a fresh read, and how often to look.
SETTLE_TIMEOUT = 20.0
POLL_INTERVAL = 2.0


def dump_data(ctrl: Controller) -> None:
    """Print controller and zone state for manual inspection (``-s``)."""
    print(ctrl.device_ip)
    print(ctrl.device_uid)
    print(f"supply={ctrl.temp_supply} mode={ctrl.mode} isOn={ctrl.is_on}")
    print(f"sleep_timer={ctrl.sleep_timer}")

    for zone in ctrl.zones:
        zone_target = (
            zone.temp_setpoint if zone.mode == Zone.Mode.AUTO else zone.mode.value
        )
        print(
            f"Name {zone.name} type:{zone.type.value} temp:{zone.temp_current} target:{zone_target} "
            f"airflow_min:{zone.airflow_min} airflow_max:{zone.airflow_max}"
        )


async def _eventually(ctrl: Controller, check: Callable[[], bool]) -> None:
    """Refresh from the bridge until *check* passes or SETTLE_TIMEOUT expires."""
    async with asyncio.timeout(SETTLE_TIMEOUT):
        while True:
            await ctrl.refresh_all()
            if check():
                return
            await asyncio.sleep(POLL_INTERVAL)


def _auto_zone(ctrl: Controller) -> Zone:
    """Return the first temperature-controlled zone, or skip the test."""
    zone = next((zone for zone in ctrl.zones if zone.type == Zone.Type.AUTO), None)
    if zone is None:
        pytest.skip("Discovered controller has no temperature-controlled zone")
    return zone


def _nudged_setpoint(ctrl: Controller, value: float) -> float:
    """Return *value* moved by 0.5 °C, staying within the controller's limits."""
    return value + 0.5 if value + 0.5 <= ctrl.temp_max else value - 0.5


@pytest.mark.hardware
async def test_all_properties_read(live_controller: Controller) -> None:
    """Every property parses from the bridge's real payloads."""
    ctrl = live_controller
    dump_data(ctrl)

    for name, attr in vars(Controller).items():
        if isinstance(attr, property):
            getattr(ctrl, name)
    for zone in ctrl.zones:
        for name, attr in vars(Zone).items():
            if isinstance(attr, property):
                getattr(zone, name)


@pytest.mark.hardware
async def test_repeated_refresh_stays_connected(live_controller: Controller) -> None:
    for _ in range(3):
        await live_controller.refresh_all()
        assert live_controller.connected


@pytest.mark.hardware
async def test_dump_state_is_json(live_controller: Controller) -> None:
    json.dumps(live_controller.dump_state())


@pytest.mark.hardware
async def test_system_mode_round_trip(live_controller: Controller) -> None:
    ctrl = live_controller
    old = ctrl.mode
    new = Controller.Mode.COOL if old == Controller.Mode.AUTO else Controller.Mode.AUTO

    try:
        await ctrl.set_mode(new)
        await _eventually(ctrl, lambda: ctrl.mode == new)
    finally:
        await ctrl.set_mode(old)
    await _eventually(ctrl, lambda: ctrl.mode == old)


@pytest.mark.hardware
async def test_fan_round_trip(live_controller: Controller) -> None:
    ctrl = live_controller
    old = ctrl.fan
    new = next((fan for fan in ctrl.fan_modes if fan != old), None)
    if new is None:
        pytest.skip("Controller allows only one fan mode")

    try:
        await ctrl.set_fan(new)
        await _eventually(ctrl, lambda: ctrl.fan == new)
    finally:
        await ctrl.set_fan(old)
    await _eventually(ctrl, lambda: ctrl.fan == old)


@pytest.mark.hardware
async def test_unit_setpoint_round_trip(live_controller: Controller) -> None:
    ctrl = live_controller
    old = ctrl.temp_setpoint
    if old is None:
        pytest.skip("Controller reports no unit setpoint")
    new = _nudged_setpoint(ctrl, old)

    try:
        await ctrl.set_temp_setpoint(new)
        await _eventually(ctrl, lambda: ctrl.temp_setpoint == new)
    finally:
        await ctrl.set_temp_setpoint(old)
    await _eventually(ctrl, lambda: ctrl.temp_setpoint == old)


@pytest.mark.hardware
async def test_zone_setpoint_round_trip(live_controller: Controller) -> None:
    ctrl = live_controller
    zone = _auto_zone(ctrl)
    old_mode = zone.mode
    old = zone.temp_setpoint
    if old is None:
        pytest.skip(f"Zone {zone.name} reports no setpoint")
    new = _nudged_setpoint(ctrl, old)

    try:
        await zone.set_temp_setpoint(new)
        await _eventually(ctrl, lambda: zone.temp_setpoint == new)
    finally:
        await zone.set_temp_setpoint(old)
        await zone.set_mode(old_mode)
    await _eventually(ctrl, lambda: zone.temp_setpoint == old and zone.mode == old_mode)


@pytest.mark.hardware
async def test_zone_mode_round_trip(live_controller: Controller) -> None:
    ctrl = live_controller
    zone = _auto_zone(ctrl)
    old = zone.mode
    new = Zone.Mode.CLOSE if old == Zone.Mode.OPEN else Zone.Mode.OPEN

    try:
        await zone.set_mode(new)
        await _eventually(ctrl, lambda: zone.mode == new)
    finally:
        await zone.set_mode(old)
    await _eventually(ctrl, lambda: zone.mode == old)


@pytest.mark.hardware
@pytest.mark.usefixtures("enable_power")
async def test_power(live_controller: Controller) -> None:
    power = live_controller.power
    if power is None or not power.enabled:
        pytest.skip("Discovered controller has no enabled power monitor")

    updated = await power.refresh()
    assert isinstance(updated, bool)

    enabled_channels = [
        channel
        for device in power.devices
        if device.enabled
        for channel in device.channels
        if channel.enabled
    ]
    if not enabled_channels:
        pytest.skip("Power monitor has no enabled channels")
    channel = enabled_channels[0]
    assert isinstance(channel.status_power, int)
    print(
        f"power channel {channel.name}: {channel.status_power}W "
        f"ok={channel.device.status_ok}"
    )


@pytest.mark.hardware
async def test_v2_command_interleaves_with_zone_poll(
    live_controller: Controller,
) -> None:
    """Interleave a zone-mode command with an in-progress zone poll sweep.

    Exercises the change in pizone#39 where ``_fetch_zones`` no longer holds
    ``_sending_lock`` for an entire sweep, so a queued command can now run
    between two sequential Type=2 reads instead of waiting for the whole
    sweep to finish. Offline fakes can't tell us whether a write landing
    mid-sweep confuses real firmware, so this needs a live V2 bridge.
    """
    ctrl = live_controller
    if ctrl._read_api != "v2":
        pytest.skip("Discovered controller is not on the V2 read/write path")
    if not ctrl.zones:
        pytest.skip("Discovered controller has no zones")

    zone = ctrl.zones[0]
    old_mode = zone.mode
    alt_mode = Zone.Mode.OPEN if old_mode != Zone.Mode.OPEN else Zone.Mode.CLOSE

    try:
        for i in range(8):
            target = alt_mode if i % 2 == 0 else old_mode
            poll = asyncio.create_task(ctrl.refresh_zones())
            await asyncio.sleep(0)  # let the sweep start before the write lands
            await zone.set_mode(target)
            assert zone.mode == target
            await poll
            assert ctrl.connected

        # Confirm the final state actually stuck on a fresh, independent read.
        await ctrl.refresh_zones()
        assert zone.mode == old_mode
    finally:
        await zone.set_mode(old_mode)
