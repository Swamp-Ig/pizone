# pylint: disable=protected-access
import json

import pytest

from pizone import Power


class MockPowerController:
    """Minimal controller stub for Power unit tests."""

    def __init__(self, responses: dict[int, dict[str, object]]) -> None:
        self._responses = responses
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def _send_command_async(
        self, command: str, data: dict[str, object]
    ) -> str:
        self.sent.append((command, data))
        req_type = data["PowerRequest"]["Type"]  # type: ignore[index]
        return json.dumps(self._responses[req_type])


POWER_CONFIG = {
    "Enabled": 1,
    "Tag1": "Grid",
    "Tag2": "Monitor",
    "Voltage": 240,
    "PF": 100,
    "CostOfPower": 2520,
    "Emissions": 870,
    "Devices": [
        {
            "Enabled": 1,
            "Name": "Grid",
            "Channels": [
                {
                    "Enabled": 1,
                    "Name": "Grid",
                    "GrNo": 1,
                    "Generate": 0,
                    "AddToTotal": 1,
                },
                {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
            ],
        },
        *[
            {
                "Enabled": 0,
                "Name": "",
                "Channels": [
                    {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                    {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                    {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                ],
            }
            for _ in range(4)
        ],
    ],
}

POWER_STATUS = {
    "LastReadingNo": 394,
    "Dev": [
        {
            "Ok": 0,
            "Batt": 3,
            "Ch": [
                {"Pwr": 0},
                {"Pwr": 0},
                {"Pwr": 0},
            ],
        },
        *[
            {
                "Ok": 1,
                "Batt": 3,
                "Ch": [
                    {"Pwr": 0},
                    {"Pwr": 0},
                    {"Pwr": 0},
                ],
            }
            for _ in range(4)
        ],
    ],
}


@pytest.mark.asyncio
async def test_power_init_and_status_last_reading() -> None:
    controller = MockPowerController(
        {
            1: {"PowerMonitorConfig": POWER_CONFIG},
            2: {"PowerMonitorStatus": POWER_STATUS},
        }
    )
    power = Power(controller)

    await power.init()
    assert power.enabled == 1
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
    power = Power(controller)
    power._status = {"LastReadingNo": 394}

    changed = await power.refresh()
    assert changed is True
    assert power.status_last_reading == 395
