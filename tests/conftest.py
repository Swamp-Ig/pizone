# pylint: disable=protected-access
# pylint: disable=protected-access
from asyncio import Event, wait_for
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pizone import Controller, Listener
from pizone.discovery import CHANGED_SYSTEM, CHANGED_ZONES, DiscoveryService


class MockController(Controller):
    def __init__(
        self,
        service,
        event_coordinator,
        device_uid: str,
        device_ip: str,
        is_v2: bool,
        is_ipower: bool,
    ) -> None:
        super().__init__(
            service,
            event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )
        from .resources import SYSTEMS

        self.resources = deepcopy(SYSTEMS[device_uid])  # type: dict[str, Any]
        self.sent: list[tuple[str, Any]] = []
        self._connected = True

    def _check_disconnected(self) -> None:
        if self._fail_exception:
            raise ConnectionError(
                "Unable to connect to the controller"
            ) from self._fail_exception

    def _check_discovery_connected(self) -> None:
        if not self._connected or not self.discovery.connected:
            ex = OSError("Not Connected")
            self._failed_connection(ex)
            raise ConnectionError("Explicitly Disconnected") from ex

    async def _get_resource(self, resource: str):
        """Mock out the network IO for _get_resource."""
        self._check_discovery_connected()
        result = self.resources.get(resource)
        if result:
            return deepcopy(result)
        raise ConnectionError(f"Mock resource '{resource}' not available")

    async def _send_command_async(self, command: str, data: Any):
        """Mock out the network IO for _send_command."""
        self._check_disconnected()
        self._check_discovery_connected()
        self.sent.append((command, data))

    async def change_system_state(self, state: str, value: Any) -> None:
        self.resources["SystemSettings"][state] = value
        await self.discovery._process_datagram(CHANGED_SYSTEM, ("8.8.8.8", 12107))

    async def change_zone_state(self, zone: int, state: str, value: Any) -> None:
        idx = zone % 4
        segment = f"Zones{zone - idx}_{zone - idx + 4}"
        self.resources[segment][idx][state] = value
        await self.discovery._process_datagram(CHANGED_ZONES, ("8.8.8.8", 12107))


class MockDiscoveryService(DiscoveryService):
    def __init__(self) -> None:
        super().__init__()
        self._send_broadcasts = AsyncMock()  # type: ignore
        self.datagram_received = AsyncMock()  # type: ignore
        self.connected = True

    def _create_controller(self, device_uid, device_ip, is_v2, is_ipower):
        return MockController(
            self,
            self._event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )


async def _register_mock_service(svc, datagram: str):
    class ListenerConnected(Listener):
        def __init__(self) -> None:
            self._controller = None
            self._connected = Event()

        def controller_discovered(self, _ctrl):
            if self._controller is not None:
                return
            self._controller = _ctrl
            self._connected.set()

        async def await_controller(self):
            await wait_for(self._connected.wait(), 5)
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
async def service():
    """Async fixture providing a mock discovery service with a pre-discovered controller."""
    service = MockDiscoveryService()

    await _register_mock_service(
        service, b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate"
    )

    yield service

    await service.close()


@pytest.fixture
async def legacy_service():
    """Async fixture providing a mock discovery service with legacy discovery message."""
    service = MockDiscoveryService()

    await _register_mock_service(service, b"ASPort_12107,Mac_000000001,IP_8.8.8.8")

    yield service

    await service.close()
