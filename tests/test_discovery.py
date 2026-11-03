"""Tests for discovery, controller refresh, and reconnect behavior."""

import asyncio
from asyncio import sleep
from typing import cast
from unittest.mock import MagicMock, patch

from aiohttp import ClientSession
import pytest

from pizone import Controller, ControllerCommandError, ControllerEndpoint, Zone
from pizone.discovery import DiscoveryService

from .conftest import MockController, MockDiscoveryService
from .http_fakes import FakeHttpResponse, FakeHttpSession


@pytest.mark.asyncio
@patch.object(DiscoveryService, "_get_broadcasts")
async def test_scan_sends_on_local_broadcasts(broadcasts: MagicMock) -> None:
    broadcasts.return_value = []

    async with DiscoveryService() as service:
        assert not broadcasts.called
        await service.scan()
        assert broadcasts.called


@pytest.mark.asyncio
@patch.object(DiscoveryService, "_send_broadcasts")
async def test_start_discovery_sends_nothing(send_broadcasts: MagicMock) -> None:
    async with DiscoveryService():
        assert not send_broadcasts.called


@pytest.mark.asyncio
async def test_connection_lost(
    service: MockDiscoveryService, caplog: pytest.LogCaptureFixture
) -> None:
    service._on_connection_lost(OSError("Nonspecific"))
    await sleep(0)

    assert len(caplog.messages) == 1
    assert caplog.messages[0] == "Connection Lost unexpectedly: OSError('Nonspecific')"

    assert service.is_closed


@pytest.mark.asyncio
async def test_close_excludes_current_task() -> None:
    """Tracked close() must not cancel or deadlock on itself."""
    service = MockDiscoveryService()
    await service.start_discovery()
    close_task = service.create_task(service.close())
    await sleep(0)
    assert not close_task.cancelled()
    await close_task
    assert service.is_closed


@pytest.mark.asyncio
async def test_concurrent_close_idempotent() -> None:
    """Concurrent close() calls coalesce without raising."""
    service = MockDiscoveryService()
    await service.start_discovery()
    await asyncio.gather(service.close(), service.close())
    assert service.is_closed


@pytest.mark.asyncio
async def test_discovery(service: MockDiscoveryService) -> None:
    assert len(service._controllers) == 1
    assert "000000001" in service._controllers

    controller = cast(MockController, service._controllers["000000001"])
    assert controller.device_uid == "000000001"
    assert controller.device_ip == "8.8.8.8"
    assert controller.mode == Controller.Mode.HEAT

    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[-1] == ("SystemMODE", {"SystemMODE": "cool"})
    assert controller.mode == Controller.Mode.COOL


@pytest.mark.asyncio
async def test_ip_addr_change(service: MockDiscoveryService) -> None:
    """Verify that IP address changes are handled."""
    controller = cast(MockController, service._controllers["000000001"])
    assert controller.device_uid == "000000001"
    assert controller.device_ip == "8.8.8.8"

    service._process_datagram(
        b"ASPort_12107,Mac_000000001,IP_8.8.8.4,iZone,iLight,iDrate", ("8.8.8.4", 12107)
    )
    await sleep(0)

    assert controller.device_ip == "8.8.8.4"


@pytest.mark.asyncio
async def test_refresh_zones_supports_zone_extender_group(
    service: MockDiscoveryService,
) -> None:
    """Verify that zones 13 and 14 are fetched from the extender endpoint."""
    controller = cast(MockController, service._controllers["000000001"])
    controller.resources["SystemSettings"]["NoOfZones"] = 14
    controller._system_settings["NoOfZones"] = 14
    controller.resources["Zones13_14"] = [
        {
            "AirStreamDeviceUId": "000000001",
            "Id": 0,
            "Index": 12,
            "Name": "Zone 13",
            "Type": "opcl",
            "Mode": "open",
            "SetPoint": 23,
            "Temp": 0,
            "MaxAir": 100,
            "MinAir": 0,
            "Const": 255,
            "ConstA": "false",
            "DmpFlt": "true",
            "Master": "false",
            "iSense": "off",
        },
        {
            "AirStreamDeviceUId": "000000001",
            "Id": 0,
            "Index": 13,
            "Name": "Zone 14",
            "Type": "opcl",
            "Mode": "open",
            "SetPoint": 23,
            "Temp": 0,
            "MaxAir": 100,
            "MinAir": 0,
            "Const": 255,
            "ConstA": "false",
            "DmpFlt": "true",
            "Master": "false",
            "iSense": "off",
        },
    ]
    controller.zones.extend(Zone(controller, i) for i in range(8, 14))

    await controller.refresh_zones()

    assert controller.zones[12].name == "Zone 13"
    assert controller.zones[13].name == "Zone 14"


@pytest.mark.asyncio
async def test_refresh_restores_connection(service: MockDiscoveryService) -> None:
    """Successful refresh clears a prior connection failure."""
    controller = cast(MockController, service._controllers["000000001"])
    controller._set_bridge_ok(False)
    assert not controller.connected

    await controller.refresh_system()

    assert controller.connected


@pytest.mark.asyncio
async def test_disconnected_reads_return_cached_state(
    service: MockDiscoveryService,
) -> None:
    """Sync property reads use cached data and do not raise when disconnected."""
    controller = cast(MockController, service._controllers["000000001"])
    assert controller.mode == Controller.Mode.HEAT

    controller._set_bridge_ok(False)
    assert not controller.connected

    assert controller.mode == Controller.Mode.HEAT
    assert controller.zones[0].name == "LIVING"

    with pytest.raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)


@pytest.mark.asyncio
async def test_changed_system_datagram(service: MockDiscoveryService) -> None:
    """iZoneChanged_System is recognized and ignored (no refresh)."""
    controller = cast(MockController, service._controllers["000000001"])
    controller.resources["SystemSettings"]["SysMode"] = "cool"
    controller._system_settings["SysMode"] = "heat"

    service._process_datagram(
        b"iZoneChanged_System",
        (controller.device_ip, 12107),
    )
    await asyncio.sleep(0)

    assert controller.mode == Controller.Mode.HEAT


@pytest.mark.asyncio
async def test_changed_zones_datagram(service: MockDiscoveryService) -> None:
    """iZoneChanged_Zones is recognized and ignored (no refresh)."""
    controller = cast(MockController, service._controllers["000000001"])
    controller.resources["Zones1_4"][0]["Name"] = "UPDATED"
    controller.zones[0]._zone_data["Name"] = "LIVING"

    service._process_datagram(
        b"iZoneChanged_Zones",
        (controller.device_ip, 12107),
    )
    await asyncio.sleep(0.1)

    assert controller.zones[0].name == "LIVING"


@pytest.mark.asyncio
async def test_x_ac_flag_discovery() -> None:
    """Bridges that report X for the AC slot should still be discovered."""
    svc = MockDiscoveryService()
    datagram = b"ASPort_12107,Mac_000025841,IP_10.0.0.90,X,iLight,iDrate,iPower"

    svc._process_datagram(datagram, ("10.0.0.90", 12107))

    assert svc._known_endpoints == {
        "000025841": ControllerEndpoint(uid="000025841", host="10.0.0.90")
    }
    await svc.close()


@pytest.mark.asyncio
async def test_invalid_discovery_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = MockDiscoveryService()
    await service.start_discovery()

    service._process_datagram(b"not-a-discovery-message", ("10.0.0.90", 12107))

    assert len(service._controllers) == 0
    assert any("Invalid Message Received" in message for message in caplog.messages)

    await service.close()


@pytest.mark.asyncio
async def test_send_command_error_body_restores_connection(
    service: MockDiscoveryService,
) -> None:
    controller = Controller(
        service,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    controller._set_bridge_ok(False)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(FakeHttpResponse(200, "{ERROR:notImplementedYet")),
    )
    try:
        with pytest.raises(ControllerCommandError):
            await controller.set_on(False)
    finally:
        service._session = original_session

    assert controller.connected


@pytest.mark.asyncio
async def test_send_command_http_404_restores_connection(
    service: MockDiscoveryService,
) -> None:
    controller = Controller(
        service,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    controller._set_bridge_ok(False)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(FakeHttpResponse(404, "404: File not found")),
    )
    try:
        with pytest.raises(ControllerCommandError):
            await controller.set_on(False)
    finally:
        service._session = original_session

    assert controller.connected
