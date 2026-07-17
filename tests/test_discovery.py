"""Tests for discovery, controller refresh, and reconnect behavior."""

# disposition: 1.4 | deprecate  (untagged = keep)
#   keep      — default; no tag required. Shared dual-track / pathway-agnostic tests.
#   1.4       — new consumer-driven discovery / refresh API
#   deprecate — legacy track; grep and delete when dual-track ends
#               (sticky within a function until the next disposition tag).

import asyncio
from asyncio import sleep
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientSession
import pytest

from pizone import Controller, ControllerCommandError, Listener, Zone, discovery
from pizone.discovery import DiscoveryService

from .conftest import MockController, MockDiscoveryService, _register_mock_service
from .http_fakes import FakeHttpResponse, FakeHttpSession

# disposition: deprecate
@pytest.mark.asyncio
@patch.object(DiscoveryService, "_get_broadcasts")
async def test_broadcast(broadcasts: MagicMock) -> None:
    broadcasts.return_value = []

    async with discovery():
        assert broadcasts.called

# disposition: deprecate
@pytest.mark.asyncio
@patch.object(DiscoveryService, "_send_broadcasts")
async def test_messages_sent(send_broadcasts: MagicMock) -> None:
    async with discovery():
        assert send_broadcasts.called

# disposition: deprecate
@pytest.mark.asyncio
@patch.object(DiscoveryService, "_send_broadcasts")
async def test_rescan(send: MagicMock) -> None:
    async with discovery() as service:
        assert not service.is_closed
        assert send.call_count == 1

        await service._rescan()
        await sleep(0)
        assert send.call_count == 2

    assert service.is_closed

# disposition: deprecate
@pytest.mark.asyncio
async def test_fail_on_connect(caplog: pytest.LogCaptureFixture) -> None:
    service = MockDiscoveryService()
    service.connected = False

    async with service:
        service._process_datagram(
            b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate",
            ("8.8.8.8", 12107),
        )
        await sleep(0)

    assert len(caplog.messages) == 1
    assert caplog.messages[0][:41] == "Can't connect to discovered server at IP "
    assert not service._controllers

# disposition: deprecate
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

# disposition: deprecate
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

# disposition: deprecate
@pytest.mark.asyncio
async def test_legacy_discovery(legacy_service: MockDiscoveryService) -> None:
    service = legacy_service

    assert len(service._controllers) == 1
    assert "000000001" in service._controllers

    controller = cast(MockController, service._controllers["000000001"])
    assert controller.device_uid == "000000001"
    assert controller.device_ip == "8.8.8.8"
    assert controller.mode == Controller.Mode.HEAT

    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[-1] == ("SystemMODE", {"SystemMODE": "cool"})
    assert controller.mode == Controller.Mode.COOL

# disposition: deprecate
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

    await controller._refresh_zones(notify=False)

    assert controller.zones[12].name == "Zone 13"
    assert controller.zones[13].name == "Zone 14"


@pytest.mark.asyncio
async def test_refresh_restores_connection(service: MockDiscoveryService) -> None:
    """Successful refresh clears a prior connection failure."""
    controller = cast(MockController, service._controllers["000000001"])
    controller._failed_connection(ConnectionError("Fake connection error"))
    assert not controller.connected

    await controller._refresh_system(notify=False)

    assert controller.connected


@pytest.mark.asyncio
async def test_disconnected_reads_return_cached_state(
    service: MockDiscoveryService,
) -> None:
    """Sync property reads use cached data and do not raise when disconnected."""
    controller = cast(MockController, service._controllers["000000001"])
    assert controller.mode == Controller.Mode.HEAT

    controller._failed_connection(ConnectionError("Fake connection error"))
    assert not controller.connected

    assert controller.mode == Controller.Mode.HEAT
    assert controller.zones[0].name == "LIVING"

    with pytest.raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

# disposition: deprecate
@pytest.mark.asyncio
async def test_reconnect(
    service: MockDiscoveryService, caplog: pytest.LogCaptureFixture
) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    assert controller.device_uid == "000000001"
    assert controller.mode == Controller.Mode.HEAT

    controller._failed_connection(ConnectionError("Fake connection error"))
    controller.sent.clear()
    with pytest.raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert caplog.messages[0][:30] == "Connection to controller lost:"
    assert not controller.sent

    service._process_datagram(
        b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate", ("8.8.8.8", 12107)
    )

    await sleep(0.1)

    # Reconnect OK
    assert caplog.messages[1][:23] == "Controller reconnected:"
    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[-1] == ("SystemMODE", {"SystemMODE": "cool"})

# disposition: deprecate
@pytest.mark.asyncio
async def test_reconnect_listener(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    calls: list[tuple[str, Controller] | tuple[str, Controller, Exception]] = []

    class TestListener(Listener):
        def controller_discovered(self, ctrl: Controller) -> None:
            calls.append(("discovered", ctrl))

        def controller_disconnected(self, ctrl: Controller, ex: Exception) -> None:
            calls.append(("disconnected", ctrl, ex))

        def controller_reconnected(self, ctrl: Controller) -> None:
            calls.append(("reconnected", ctrl))

    listener = TestListener()

    service.add_listener(listener)
    await sleep(0)

    assert len(calls) == 1
    assert calls[-1] == ("discovered", controller)

    controller._failed_connection(ConnectionError("Fake connection error"))
    with pytest.raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert len(calls) == 2
    assert calls[-1][0:2] == ("disconnected", controller)

    service._process_datagram(
        b"ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate", ("8.8.8.8", 12107)
    )
    await sleep(0.1)

    assert len(calls) == 3
    assert calls[-1] == ("reconnected", controller)

    service._process_datagram(
        b"ASPort_12107,Mac_000000002,IP_8.8.8.4,iZone,iLight,iDrate", ("8.8.8.8", 12107)
    )
    await sleep(0.1)
    controller2 = cast(MockController, service._controllers["000000002"])

    assert len(calls) == 4
    assert calls[-1] == ("discovered", controller2)

    service.remove_listener(listener)

    controller._failed_connection(ConnectionError("Fake connection error"))
    with pytest.raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert len(calls) == 4

# disposition: deprecate
@pytest.mark.asyncio
async def test_rescan_cooldown_suppression(
    service: MockDiscoveryService,
) -> None:
    """Verify that rescan is suppressed within the cool-down window."""
    with patch.object(
        service, "_rescan", AsyncMock(side_effect=service._rescan)
    ) as rescan:
        await service.fetch_controllers(timeout=0.1)
        assert rescan.call_count == 1

        await service.fetch_controllers(timeout=0.1)
        assert rescan.call_count == 1

# disposition: deprecate
@pytest.mark.asyncio
async def test_fetch_controller_already_known(
    service: MockDiscoveryService,
) -> None:
    """Verify that fetch_controller returns immediately for known controller."""
    controller = await service.fetch_controller("000000001", timeout=1.0)
    assert controller is not None
    assert controller.device_uid == "000000001"

# disposition: deprecate
@pytest.mark.asyncio
async def test_fetch_controller_unknown_no_timeout(
    service: MockDiscoveryService,
) -> None:
    """Verify that fetch_controller returns None for unknown controller without timeout."""
    controller = await service.fetch_controller("unknown_uid")
    assert controller is None

# disposition: deprecate
@pytest.mark.asyncio
async def test_fetch_controller_unknown_timeout_expires(
    service: MockDiscoveryService,
) -> None:
    """Verify that fetch_controller returns None when timeout expires."""
    controller = await service.fetch_controller("unknown_uid", timeout=0.1)
    assert controller is None

# disposition: deprecate
@pytest.mark.asyncio
async def test_fetch_controllers_no_timeout(
    service: MockDiscoveryService,
) -> None:
    """Verify that fetch_controllers returns snapshot without timeout."""
    controllers = await service.fetch_controllers()
    assert len(controllers) == 1
    assert "000000001" in controllers

# disposition: deprecate
@pytest.mark.asyncio
async def test_fetch_controllers_with_timeout(
    service: MockDiscoveryService,
) -> None:
    """Verify that fetch_controllers waits when timeout is specified."""
    controllers = await service.fetch_controllers(timeout=0.1)
    assert len(controllers) == 1
    assert "000000001" in controllers

# disposition: deprecate
@pytest.mark.asyncio
async def test_listener_controller_discovered_on_add(
    service: MockDiscoveryService,
) -> None:
    """Verify that listener receives existing controllers on add."""
    calls = []

    class TestListener(Listener):
        def controller_discovered(self, ctrl: Controller) -> None:
            calls.append(("discovered", ctrl.device_uid))

    listener = TestListener()
    service.add_listener(listener)
    await sleep(0)

    # Should have been called with the existing controller
    assert len(calls) == 1
    assert calls[0] == ("discovered", "000000001")

# disposition: deprecate
@pytest.mark.asyncio
async def test_failed_init_deduplicated() -> None:
    service = MockDiscoveryService()
    initialize = AsyncMock(side_effect=ConnectionError("init failed"))

    async with service:
        with patch.object(Controller, "_initialize", initialize):
            datagram = b"ASPort_12107,Mac_000000002,IP_9.9.9.9,iZone"
            service._process_datagram(datagram, ("9.9.9.9", 12107))
            service._process_datagram(datagram, ("9.9.9.9", 12107))
            await sleep(0.1)

    assert initialize.call_count == 1
    assert "000000002" not in service._controllers

# disposition: deprecate
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

# disposition: deprecate
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

# disposition: deprecate
@pytest.mark.asyncio
async def test_x_ac_flag_discovery() -> None:
    """Bridges that report X for the AC slot should still be discovered."""
    svc = MockDiscoveryService()
    datagram = b"ASPort_12107,Mac_000025841,IP_10.0.0.90,X,iLight,iDrate,iPower"

    await _register_mock_service(svc, datagram)

    assert "000025841" in svc._controllers
    controller = cast(MockController, svc._controllers["000025841"])
    assert controller.device_ip == "10.0.0.90"
    assert controller.is_v2 is False
    # Power is gated off by default (ENABLE_POWER); iPower in the datagram is ignored.
    assert controller.power is None
    assert controller.is_ipower is False

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

# disposition: deprecate
@pytest.mark.asyncio
async def test_ipower_discovery(ipower_service: MockDiscoveryService) -> None:
    assert "000000003" in ipower_service._controllers
    controller = cast(MockController, ipower_service._controllers["000000003"])
    assert controller.power is not None
    assert controller.power.enabled is True

# disposition: deprecate
@pytest.mark.asyncio
async def test_discovery_factory_registers_listener() -> None:
    class TestListener(Listener):
        def controller_discovered(self, ctrl: Controller) -> None:
            del ctrl

    listener = TestListener()
    service = discovery(listener)
    assert listener in service._listeners

# disposition: deprecate
@pytest.mark.asyncio
async def test_retry_connection(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    controller._failed_connection(ConnectionError("Fake connection error"))
    assert not controller.connected

    await controller._retry_connection()

    assert controller.connected is True


class _FakeHttpResponse(FakeHttpResponse):
    """Backward-compatible alias for discovery POST error tests."""


class _FakeHttpSession(FakeHttpSession):
    """Backward-compatible alias for discovery POST error tests."""


@pytest.mark.asyncio
async def test_send_command_error_body_restores_connection(
    service: MockDiscoveryService,
) -> None:
    controller = Controller.from_discovery(
        service,
        service._event_coordinator,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    controller._initialized = True
    controller._failed_connection(ConnectionError("Fake connection error"))
    original_session = service._session
    service._session = cast(
        ClientSession,
        _FakeHttpSession(_FakeHttpResponse(200, "{ERROR:notImplementedYet")),
    )
    try:
        with pytest.raises(ControllerCommandError):
            await controller._send_command_async(
                "PowerRequest", {"PowerRequest": {"Type": 99, "No": 0, "No1": 0}}
            )
    finally:
        service._session = original_session

    assert controller.connected


@pytest.mark.asyncio
async def test_send_command_http_404_restores_connection(
    service: MockDiscoveryService,
) -> None:
    controller = Controller.from_discovery(
        service,
        service._event_coordinator,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    controller._initialized = True
    controller._failed_connection(ConnectionError("Fake connection error"))
    original_session = service._session
    service._session = cast(
        ClientSession,
        _FakeHttpSession(_FakeHttpResponse(404, "404: File not found")),
    )
    try:
        with pytest.raises(ControllerCommandError):
            await controller._send_command_async(
                "NoSuchCommand", {"NoSuchCommand": "x"}
            )
    finally:
        service._session = original_session

    assert controller.connected

# disposition: deprecate
@pytest.mark.asyncio
async def test_send_command_error_fires_reconnected_listener(
    service: MockDiscoveryService,
) -> None:
    calls: list[tuple[str, Controller]] = []

    class TestListener(Listener):
        def controller_reconnected(self, ctrl: Controller) -> None:
            calls.append(("reconnected", ctrl))

    controller = Controller.from_discovery(
        service,
        service._event_coordinator,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    controller._initialized = True
    service.add_listener(TestListener())
    controller._failed_connection(ConnectionError("Fake connection error"))
    original_session = service._session
    service._session = cast(
        ClientSession,
        _FakeHttpSession(_FakeHttpResponse(200, "{ERROR:notImplementedYet")),
    )
    try:
        with pytest.raises(ControllerCommandError):
            await controller._send_command_async(
                "PowerRequest", {"PowerRequest": {"Type": 99, "No": 0, "No1": 0}}
            )
    finally:
        service._session = original_session

    assert calls[-1] == ("reconnected", controller)
