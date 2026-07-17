"""Tests for iPower configuration and status handling."""

# disposition: 1.4 | deprecate  (untagged = keep)
#   keep      — default; no tag required. Shared dual-track / pathway-agnostic tests.
#   1.4       — new consumer-driven discovery / refresh API
#   deprecate — legacy track; grep and delete when dual-track ends
#               (sticky within a function until the next disposition tag).

import json
from typing import Any, cast

import pytest

from pizone import BatteryLevel, Controller, Power
from pizone.discovery import Listener

from .power_data import POWER_CONFIG, POWER_STATUS


class _PowerListener(Listener):
    def __init__(self) -> None:
        self.power_updates = 0

    def power_update(self, _controller: Controller) -> None:
        self.power_updates += 1


class MockPowerController:
    """Minimal controller stub for Power unit tests."""

    def __init__(
        self,
        responses: dict[int, dict[str, object]],
        *,
        bridge_connected: bool = True,
        listener: _PowerListener | None = None,
    ) -> None:
        self._responses = responses
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._bridge_ok = bridge_connected
        self._event_coordinator = listener or _PowerListener()

    @property
    def bridge_connected(self) -> bool:
        return self._bridge_ok

    async def _http_post(self, command: str, data: dict[str, Any]) -> str:
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
    assert power.connected is True
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


@pytest.mark.asyncio
async def test_power_connected_false_when_bridge_down() -> None:
    controller = MockPowerController(
        {1: {"PowerMonitorConfig": POWER_CONFIG}},
        bridge_connected=False,
    )
    power = Power(cast(Controller, controller))

    with pytest.raises(ConnectionError, match="Bridge not connected"):
        await power.init()

    assert power.connected is False


@pytest.mark.asyncio
async def test_power_connected_restored_on_recovery() -> None:
    listener = _PowerListener()
    controller = MockPowerController(
        {
            1: {"PowerMonitorConfig": POWER_CONFIG},
            2: {"PowerMonitorStatus": POWER_STATUS},
        },
        listener=listener,
    )
    power = Power(cast(Controller, controller))
    await power.init()

    controller._bridge_ok = False
    controller._responses = {}
    with pytest.raises(ConnectionError):
        await power.refresh()
    assert power.connected is False
    assert listener.power_updates == 1

    controller._bridge_ok = True
    controller._responses = {2: {"PowerMonitorStatus": POWER_STATUS}}
    await power.refresh()
    assert power.connected is True
    assert listener.power_updates == 2
