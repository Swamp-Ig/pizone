"""Shared test fixtures and network-free controller doubles."""

# pylint: disable=protected-access
from asyncio import Event, wait_for
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, cast

import pytest

from pizone import Controller, Listener
from pizone.discovery import DiscoveryService

from .resources import SYSTEMS


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
        self.resources = deepcopy(SYSTEMS[device_uid])  # type: dict[str, Any]
        self.sent: list[tuple[str, Any]] = []
        self._connected = True

    def _check_discovery_connected(self) -> None:
        service = cast(MockDiscoveryService, self.discovery)
        if not self._connected or not service.connected:
            ex = OSError("Not Connected")
            self._failed_connection(ex)
            raise ConnectionError("Explicitly Disconnected") from ex

    async def _get_resource(self, resource: str) -> Any:
        """Mock out the network IO for _get_resource."""
        self._check_discovery_connected()
        result = self.resources.get(resource)
        if result:
            self._restored_connection()
            return deepcopy(result)
        raise ConnectionError(f"Mock resource '{resource}' not available")

    async def _send_command_async(
        self, command: str, data: dict[str, Any]
    ) -> str:
        """Mock out the network IO for _send_command."""
        if self._fail_exception:
            raise ConnectionError(
                "Unable to connect to the controller"
            ) from self._fail_exception
        self._check_discovery_connected()
        self.sent.append((command, data))
        self._restored_connection()
        return ""


class MockDiscoveryService(DiscoveryService):
    """Discovery service double that does not send UDP broadcasts."""

    def __init__(self) -> None:
        super().__init__()
        self.connected: bool = True

    def _send_broadcasts(self) -> None:
        """Avoid network traffic during tests."""

    def _create_controller(
        self, device_uid: str, device_ip: str, is_v2: bool, is_ipower: bool
    ) -> MockController:
        return MockController(
            self,
            self._event_coordinator,
            device_uid=device_uid,
            device_ip=device_ip,
            is_v2=is_v2,
            is_ipower=is_ipower,
        )


async def _register_mock_service(
    svc: MockDiscoveryService, datagram: bytes
) -> None:
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
    svc = MockDiscoveryService()

    await _register_mock_service(
        svc, b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate"
    )

    yield svc

    await svc.close()


@pytest.fixture
async def legacy_service() -> AsyncIterator[MockDiscoveryService]:
    """Async fixture providing a mock discovery service with legacy discovery message."""
    svc = MockDiscoveryService()

    await _register_mock_service(svc, b"ASPort_12107,Mac_000000001,IP_8.8.8.8")

    yield svc

    await svc.close()
