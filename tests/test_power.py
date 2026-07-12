"""Tests for iPower configuration and status handling."""

# pylint: disable=protected-access
import json
from typing import Any, cast

import pytest

from pizone import BatteryLevel, Controller, Power

from .power_data import POWER_CONFIG, POWER_STATUS


class MockPowerController:
    """Minimal controller stub for Power unit tests."""

    def __init__(self, responses: dict[int, dict[str, object]]) -> None:
        self._responses = responses
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def _send_command_async(
        self, command: str, data: dict[str, Any], *, mark_disconnected: bool = True
    ) -> str:
        del mark_disconnected
        self.sent.append((command, data))
        req_type: int = data["PowerRequest"]["Type"]
        return json.dumps(self._responses[req_type])


@pytest.mark.asyncio
async def test_power_init_and_status_last_reading() -> None:
    controller = MockPowerController(
        {
            1: {"PowerMonitorConfig": POWER_CONFIG},
            2: {"PowerMonitorStatus": POWER_STATUS},
        }
    )
    power = Power(cast(Controller, controller))

    await power.init()
    assert power.enabled is True
    assert power.voltage == 240
    assert power.groups is not None
    assert len(power.groups) == 1

    changed = await power.refresh()
    assert changed is True
    assert power.status_last_reading == 394

    changed = await power.refresh()
    assert changed is False


@pytest.mark.asyncio
async def test_power_refresh_updates_status_last_reading() -> None:
    controller = MockPowerController(
        {
            1: {"PowerMonitorConfig": POWER_CONFIG},
            2: {"PowerMonitorStatus": {**POWER_STATUS, "LastReadingNo": 395}},
        }
    )
    power = Power(cast(Controller, controller))
    power._status = {"LastReadingNo": 394}

    changed = await power.refresh()
    assert changed is True
    assert power.status_last_reading == 395


@pytest.mark.asyncio
async def test_power_nested_property_reads() -> None:
    controller = MockPowerController(
        {
            1: {"PowerMonitorConfig": POWER_CONFIG},
            2: {"PowerMonitorStatus": POWER_STATUS},
        }
    )
    power = Power(cast(Controller, controller))

    await power.init()
    await power.refresh()

    assert power.power_factor == 100
    assert power.cost_of_power == 2520
    assert power.emissions == 870

    device = power.devices[0]
    assert device.index == 0
    assert device.enabled is True
    assert device.status_ok is False
    assert device.status_batt == BatteryLevel.FULL
    assert len(device.channels) == 3

    channel = device.channels[0]
    assert channel.enabled is True
    assert channel.name == "Grid"
    assert channel.group_number == 1
    assert channel.generate is False
    assert channel.add_to_total is True
    assert channel.status_power == 1500

    assert power.groups is not None
    group = power.groups[0]
    assert group.group_number == 1
    assert group.name == "Grid"
    assert group.status_ok is False
    assert group.status_power == 1500
