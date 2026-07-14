"""Tests for the pizone 1.4 discovery API."""

# pylint: disable=protected-access
import asyncio
import sys
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession
from pytest import raises

from pizone import Controller, ControllerCommandError, ControllerEndpoint, create_discovery
from pizone.discovery import DiscoveryService

discovery_module = sys.modules["pizone.discovery"]

from .conftest import MockController, MockDiscoveryService
from .http_fakes import FakeHttpResponse, FakeHttpSession


def _system_settings(uid: str) -> dict[str, object]:
    return {
        "AirStreamDeviceUId": uid,
        "SysOn": "on",
        "SysMode": "heat",
        "SysFan": "auto",
        "NoOfZones": 0,
        "FanAuto": "disabled",
    }


def _system_settings_response(uid: str) -> FakeHttpResponse:
    return FakeHttpResponse(200, json_data=_system_settings(uid))


def _probe_result(
    uid: str, host: str
) -> tuple[ControllerEndpoint, dict[str, object]]:
    return ControllerEndpoint(uid=uid, host=host), _system_settings(uid)


@pytest.mark.asyncio
async def test_create_discovery_singleton() -> None:
    """create_discovery is one-shot and close clears the global."""
    with patch.object(
        DiscoveryService,
        "start_discovery",
        AsyncMock(),
    ):
        disco = await create_discovery()
        assert disco is discovery_module._active_discovery
        with raises(RuntimeError, match="already created"):
            await create_discovery()
        await disco.close()
    assert discovery_module._active_discovery is None


@pytest.mark.asyncio
async def test_discover_by_host() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


@pytest.mark.asyncio
async def test_discover_by_host_unreachable() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=OSError("unreachable")),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint is None
    await service.close()


@pytest.mark.asyncio
async def test_discover_by_host_uses_known_cache() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.discover_by_host("10.0.0.90")
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=OSError("should not probe")),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


@pytest.mark.asyncio
async def test_discover_by_host_raises_if_controller_exists() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=OSError("should not probe")),
    )
    with raises(RuntimeError, match="already created"):
        await service.discover_by_host("10.0.0.90")
    await service.close()


@pytest.mark.asyncio
async def test_discover_by_uid() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )

    async def scan_and_reply() -> None:
        service._process_datagram(
            b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
            ("10.0.0.90", 12107),
        )

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", side_effect=scan_and_reply),
    ):
        endpoint = await service.discover_by_uid("000025841")

    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


@pytest.mark.asyncio
async def test_discover_by_uid_raises_if_controller_exists() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    scan = AsyncMock()
    with patch.object(service, "scan", scan):
        with raises(RuntimeError, match="already created"):
            await service.discover_by_uid("000025841")
    scan.assert_not_awaited()
    await service.close()


@pytest.mark.asyncio
async def test_discover_all_invokes_callback() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    async def scan_and_reply() -> None:
        service._process_datagram(
            b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
            ("10.0.0.90", 12107),
        )

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", side_effect=scan_and_reply),
    ):
        endpoints = await service.discover_all()

    assert endpoints == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    assert discovered == endpoints
    await service.close()


@pytest.mark.asyncio
async def test_discover_all_excludes_created_controllers() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", AsyncMock()),
    ):
        endpoints = await service.discover_all()
    assert endpoints == []
    await service.close()


@pytest.mark.asyncio
async def test_discover_all_includes_closed_controllers() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    controller = await service.create_controller("000025841", "10.0.0.90")
    await controller.close()
    assert service._known_endpoints["000025841"] == ControllerEndpoint(
        uid="000025841", host="10.0.0.90"
    )
    assert "000025841" not in service._claimed_endpoints
    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", AsyncMock()),
    ):
        endpoints = await service.discover_all()
    assert endpoints == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    await service.close()


@pytest.mark.asyncio
async def test_scan_sends_broadcast() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._transport = MagicMock()
    with patch.object(service, "_send_broadcasts") as send_broadcasts:
        await service.scan()
    send_broadcasts.assert_called_once()
    await service.close()


@pytest.mark.asyncio
async def test_create_controller_success() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    session = FakeHttpSession(get_response=_system_settings_response("000025841"))
    service._session = cast(ClientSession, session)
    controller = await service.create_controller("000025841", "10.0.0.90")
    assert controller.device_uid == "000025841"
    assert controller.device_ip == "10.0.0.90"
    assert service._claimed_endpoints["000025841"] == ControllerEndpoint(
        uid="000025841", host="10.0.0.90"
    )
    assert "000025841" not in service._known_endpoints
    assert session.get_calls == 1
    await service.close()


@pytest.mark.asyncio
async def test_create_controller_raises_if_uid_exists() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    with raises(RuntimeError, match="already created"):
        await service.create_controller("000025841", "10.0.0.90")
    await service.close()


@pytest.mark.asyncio
async def test_create_controller_address_fallback() -> None:
    service = MockDiscoveryService(legacy_pathway=False)

    responses = {
        "10.0.0.1": _system_settings_response("000099999"),
        "10.0.0.90": _system_settings_response("000025841"),
    }

    class RoutingSession(FakeHttpSession):
        def get(self, url: object, **_kwargs: object) -> FakeHttpResponse:
            ip = str(url).split("//", 1)[1].split("/", 1)[0]
            return responses[ip]

    service._session = cast(ClientSession, RoutingSession())
    discover_calls = 0

    async def discover_by_uid_patched(uid: str) -> ControllerEndpoint | None:
        del uid
        nonlocal discover_calls
        discover_calls += 1
        return ControllerEndpoint(uid="000025841", host="10.0.0.90")

    with patch.object(service, "discover_by_uid", side_effect=discover_by_uid_patched):
        controller = await service.create_controller("000025841", "10.0.0.1")

    assert discover_calls == 1
    assert controller.device_ip == "10.0.0.90"
    await service.close()


@pytest.mark.asyncio
async def test_create_controller_address_changed_after_return() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    seen: list[ControllerEndpoint] = []
    returned: Controller | None = None

    async def discover_by_uid(_uid: str) -> ControllerEndpoint | None:
        return ControllerEndpoint(uid="000025841", host="10.0.0.90")

    with patch.object(service, "discover_by_uid", side_effect=discover_by_uid):
        with patch.object(
            service,
            "_probe",
            AsyncMock(
                side_effect=[
                    None,
                    _probe_result("000025841", "10.0.0.90"),
                ]
            ),
        ):
            with patch.object(
                MockController,
                "_initialize",
                AsyncMock(),
            ):
                returned = await service.create_controller(
                    "000025841",
                    "10.0.0.1",
                    on_address_changed=seen.append,
                )

    assert returned is not None
    assert returned.device_uid == "000025841"
    await asyncio.sleep(0)
    assert seen == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    await service.close()


@pytest.mark.asyncio
async def test_create_controller_no_retry_on_command_error() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    discover_by_uid = AsyncMock()
    with patch.object(service, "discover_by_uid", discover_by_uid):
        with patch.object(
            Controller,
            "_initialize",
            AsyncMock(side_effect=ControllerCommandError("rejected")),
        ):
            with raises(ControllerCommandError):
                await service.create_controller("000025841", "10.0.0.90")
    discover_by_uid.assert_not_awaited()
    await service.close()
