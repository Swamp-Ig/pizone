"""Hardware tests for a live iZone controller."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from pizone import Controller, Zone, create_discovery


@asynccontextmanager
async def _live_controller() -> AsyncIterator[Controller]:
    """Discover the first controller on the LAN and create it."""
    service = await create_discovery()
    try:
        endpoints = await service.discover_all()
        assert endpoints, "No iZone controller found on the network"
        yield await service.create_controller(endpoints[0].uid, endpoints[0].host)
    finally:
        await service.close()


def dump_data(ctrl: Controller) -> None:
    """Testing"""
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


@pytest.mark.hardware
async def test_full_stack() -> None:
    async with _live_controller() as ctrl:
        dump_data(ctrl)

        old_mode = ctrl.mode
        old_airflow_min = ctrl.zones[1].airflow_min
        old_airflow_max = ctrl.zones[1].airflow_max

        try:
            # test setting values
            mode = (
                Controller.Mode.COOL
                if old_mode == Controller.Mode.AUTO
                else Controller.Mode.AUTO
            )
            await ctrl.set_mode(mode)
            assert ctrl.mode == mode

            # test set airflow min
            nmin = 20 if old_airflow_min == 10 else 10
            await ctrl.zones[1].set_airflow_min(nmin)

            with pytest.raises(AttributeError):
                await ctrl.zones[1].set_airflow_min(41)

            with pytest.raises(AttributeError):
                await ctrl.zones[1].set_airflow_min(-1)

            with pytest.raises(AttributeError):
                await ctrl.zones[1].set_airflow_min(105)

            assert ctrl.zones[1].airflow_min == nmin

            # test set airflow max
            nmax = 80 if old_airflow_max == 90 else 90
            await ctrl.zones[1].set_airflow_max(nmax)

            with pytest.raises(AttributeError):
                await ctrl.zones[1].set_airflow_max(41)

            with pytest.raises(AttributeError):
                await ctrl.zones[1].set_airflow_max(-1)

            with pytest.raises(AttributeError):
                await ctrl.zones[1].set_airflow_max(105)

            assert ctrl.zones[1].airflow_max == nmax

            # Confirm the values stuck on a fresh read.
            await ctrl.refresh_all()

            assert ctrl.mode == mode
            assert ctrl.zones[1].airflow_min == nmin
            assert ctrl.zones[1].airflow_max == nmax

        finally:
            # Tidy everything up
            await ctrl.set_mode(old_mode)
            await ctrl.zones[1].set_airflow_min(old_airflow_min)
            await ctrl.zones[1].set_airflow_max(old_airflow_max)

        dump_data(ctrl)


@pytest.mark.hardware
@pytest.mark.usefixtures("enable_power")
async def test_power() -> None:
    async with _live_controller() as ctrl:
        assert ctrl.power is not None
        assert ctrl.power.enabled

        updated = await ctrl.power.refresh()
        assert isinstance(updated, bool)

        enabled_channels = [
            channel
            for device in ctrl.power.devices
            if device.enabled
            for channel in device.channels
            if channel.enabled
        ]
        assert enabled_channels
        channel = enabled_channels[0]
        assert isinstance(channel.status_power, int)
        print(
            f"power channel {channel.name}: {channel.status_power}W "
            f"ok={channel.device.status_ok}"
        )


@pytest.mark.hardware
async def test_v2_command_interleaves_with_zone_poll() -> None:
    """Interleave a zone-mode command with an in-progress zone poll sweep.

    Exercises the change in pizone#39 where ``_fetch_zones`` no longer holds
    ``_sending_lock`` for an entire sweep, so a queued command can now run
    between two sequential Type=2 reads instead of waiting for the whole
    sweep to finish. Offline fakes can't tell us whether a write landing
    mid-sweep confuses real firmware, so this needs a live V2 bridge.
    """
    async with _live_controller() as ctrl:
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
