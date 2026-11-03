"""Shared test fixtures and network-free controller doubles."""

from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
import json
from typing import Any, cast

import pytest

from pizone import Controller, ControllerEndpoint, power as power_mod
from pizone.discovery import DiscoveryService
from pizone.types import ControllerProbe

from .power_data import POWER_CONFIG, POWER_STATUS
from .resources import SYSTEMS


def _system_data(device_uid: str) -> dict[str, Any]:
    if device_uid in SYSTEMS:
        return deepcopy(SYSTEMS[device_uid])
    data = deepcopy(SYSTEMS["000000001"])
    data["SystemSettings"]["AirStreamDeviceUId"] = device_uid
    for key, value in data.items():
        if key.startswith("Zones"):
            for zone in value:
                zone["AirStreamDeviceUId"] = device_uid
    return data


# V1 system commands the mock bridge applies to SystemSettings, like the device.
_V1_SYSTEM_COMMAND_FIELDS = {
    "SystemON": "SysOn",
    "SystemMODE": "SysMode",
    "SystemFAN": "SysFan",
    "SleepTimer": "SleepTimer",
    "UnitSetpoint": "Setpoint",
    "FreeAir": "FreeAir",
}


class MockController(Controller):
    """Controller double backed by static response dictionaries."""

    def __init__(
        self,
        discovery_service: DiscoveryService,
        *,
        device_uid: str,
        device_ip: str,
        is_v2: bool,
        is_ipower: bool,
    ) -> None:
        super().__init__(
            discovery_service,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )
        self.resources = _system_data(device_uid)
        self.sent: list[tuple[str, Any]] = []
        self._connected = True
        self.v2_probe_response: str | None = None
        self.v2_system: dict[str, Any] | None = None
        self.v2_zones: dict[int, dict[str, Any]] = {}
        self.power_config: dict[str, Any] | None = None
        self.fail_power_types: set[int] = set()

    def _check_discovery_connected(self) -> None:
        service = cast(MockDiscoveryService, self.discovery)
        if not self._connected or not service.connected:
            self._set_bridge_ok(False)
            raise ConnectionError("Explicitly Disconnected") from OSError(
                "Not Connected"
            )

    async def _http_get(self, resource: str) -> Any:
        """Mock out the network IO for tracked GET requests."""
        self._check_discovery_connected()
        result = self.resources.get(resource)
        if result:
            return deepcopy(result)
        raise ConnectionError(f"Mock resource '{resource}' not available")

    async def _http_post(self, command: str, data: dict[str, Any]) -> str:
        """Mock out the network IO for untracked POST requests."""
        if not self._bridge_ok:
            raise ConnectionError("Unable to connect to the controller")
        self._check_discovery_connected()
        self.sent.append((command, data))
        if (field := _V1_SYSTEM_COMMAND_FIELDS.get(command)) is not None:
            self.resources["SystemSettings"][field] = data[command]
        if command == "iZoneRequestV2":
            if self.v2_probe_response is not None:
                return self.v2_probe_response
            raise ConnectionError("Unable to connect to controller")
        if command == "PowerRequest":
            req_type: int = data["PowerRequest"]["Type"]
            if req_type in self.fail_power_types:
                raise ConnectionError("Unable to connect to controller")
            if req_type == 1:
                config = (
                    self.power_config if self.power_config is not None else POWER_CONFIG
                )
                return json.dumps({"PowerMonitorConfig": config})
            if req_type == 2:
                return json.dumps({"PowerMonitorStatus": POWER_STATUS})
        return ""

    async def _http_command_v2(self, payload: dict[str, Any]) -> str:
        """Record iZoneCommandV2 payloads without network I/O."""
        if not self._bridge_ok:
            raise ConnectionError("Unable to connect to the controller")
        self._check_discovery_connected()
        self.sent.append(("iZoneCommandV2", payload))
        return ""

    async def _v2_request(self, req_type: int, no: int = 0) -> dict[str, Any] | None:
        """Serve V2 shapes from ``v2_system`` / ``v2_zones`` when configured."""
        if req_type == 1 and self.v2_system is not None:
            self._check_discovery_connected()
            if not self._bridge_ok:
                raise ConnectionError("Unable to connect to the controller")
            self.sent.append(
                (
                    "iZoneRequestV2",
                    {"iZoneV2Request": {"Type": 1, "No": 0, "No1": 0}},
                )
            )
            return deepcopy(self.v2_system)
        if req_type == 2 and no in self.v2_zones:
            self._check_discovery_connected()
            if not self._bridge_ok:
                raise ConnectionError("Unable to connect to the controller")
            self.sent.append(
                (
                    "iZoneRequestV2",
                    {"iZoneV2Request": {"Type": 2, "No": no, "No1": 0}},
                )
            )
            return deepcopy(self.v2_zones[no])
        return await super()._v2_request(req_type, no)


class MockDiscoveryService(DiscoveryService):
    """Discovery service double that does not send UDP broadcasts."""

    _controller_cls = MockController

    def __init__(self) -> None:
        super().__init__()
        self.connected: bool = True

    def _send_broadcasts(self) -> None:
        """Avoid network traffic during tests."""


async def add_mock_controller(
    svc: MockDiscoveryService, device_uid: str, device_ip: str
) -> MockController:
    """Create, initialize and claim a controller the way ``create_controller`` does."""
    settings = _system_data(device_uid)["SystemSettings"]
    probe = ControllerProbe(
        endpoint=ControllerEndpoint(uid=device_uid, host=device_ip),
        system_settings=settings,
        read_api="v1",
    )
    controller = await MockController.create(svc, probe=probe)
    svc._claim(controller)
    return controller


@pytest.fixture
def enable_power() -> Iterator[None]:
    """Opt into iPower probe/refresh for the duration of a test."""
    previous = power_mod.ENABLE_POWER
    power_mod.ENABLE_POWER = True
    try:
        yield
    finally:
        power_mod.ENABLE_POWER = previous


@pytest.fixture
async def service() -> AsyncIterator[MockDiscoveryService]:
    """Mock discovery service with a claimed, initialized controller."""
    svc = MockDiscoveryService()
    await add_mock_controller(svc, "000000001", "8.8.8.8")

    yield svc

    await svc.close()


@pytest.fixture
async def ipower_service(
    enable_power: None,
) -> AsyncIterator[MockDiscoveryService]:
    """Mock discovery service with an initialized iPower controller."""
    del enable_power
    svc = MockDiscoveryService()
    await add_mock_controller(svc, "000000003", "10.0.0.1")

    yield svc

    await svc.close()
