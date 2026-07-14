"""Shared test fixtures and network-free controller doubles."""

from asyncio import Event, wait_for
from collections.abc import AsyncIterator
from copy import deepcopy
import json
from typing import Any, cast

import pytest

from pizone import Controller, Listener
from pizone.discovery import DiscoveryService

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


class MockController(Controller):
    """Controller double backed by static response dictionaries."""

    def __init__(
        self,
        discovery_service: DiscoveryService,
        event_coordinator: Listener,
        device_uid: str,
        device_ip: str,
        is_v2: bool,
        is_ipower: bool,
    ) -> None:
        super().__init__(
            discovery_service,
            event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )
        self.resources = _system_data(device_uid)
        self.sent: list[tuple[str, Any]] = []
        self._connected = True
        self.v2_probe_response: str | None = None
        self.power_config: dict[str, Any] | None = None
        self.fail_power_types: set[int] = set()

    def _check_discovery_connected(self) -> None:
        service = cast(MockDiscoveryService, self.discovery)
        if not self._connected or not service.connected:
            ex = OSError("Not Connected")
            self._failed_connection(ex)
            raise ConnectionError("Explicitly Disconnected") from ex

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


class MockDiscoveryService(DiscoveryService):
    """Discovery service double that does not send UDP broadcasts."""

    _controller_cls = MockController

    def __init__(self, *, legacy_pathway: bool = True) -> None:
        super().__init__()
        self.connected: bool = True
        self._legacy_pathway = legacy_pathway

    def _send_broadcasts(self) -> None:
        """Avoid network traffic during tests."""


async def _register_mock_service(svc: MockDiscoveryService, datagram: bytes) -> None:
    class ListenerConnected(Listener):
        def __init__(self) -> None:
            self._controller: Controller | None = None
            self._connected = Event()

        def controller_discovered(self, ctrl: Controller) -> None:
            if self._controller is not None:
                return
            self._controller = ctrl
            self._connected.set()

        async def await_controller(self) -> Controller:
            await wait_for(self._connected.wait(), 5)
            assert self._controller is not None
            return self._controller

    listener = ListenerConnected()
    svc.add_listener(listener)

    await svc.start_discovery()

    svc._process_datagram(
        datagram,
        ("8.8.8.8", 12107),
    )

    await listener.await_controller()


@pytest.fixture
async def service() -> AsyncIterator[MockDiscoveryService]:
    """Async fixture providing a mock discovery service with a pre-discovered controller."""
    svc = MockDiscoveryService(legacy_pathway=True)

    await _register_mock_service(
        svc, b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate"
    )

    yield svc

    await svc.close()


@pytest.fixture
async def legacy_service() -> AsyncIterator[MockDiscoveryService]:
    """Async fixture providing a mock discovery service with legacy discovery message."""
    svc = MockDiscoveryService(legacy_pathway=True)

    await _register_mock_service(svc, b"ASPort_12107,Mac_000000001,IP_8.8.8.8")

    yield svc

    await svc.close()


@pytest.fixture
async def ipower_service() -> AsyncIterator[MockDiscoveryService]:
    """Mock discovery service with an iPower-enabled controller."""
    svc = MockDiscoveryService(legacy_pathway=True)

    await _register_mock_service(
        svc,
        b"ASPort_12107,Mac_000000003,IP_10.0.0.1,iZone,iPower",
    )

    yield svc

    await svc.close()
