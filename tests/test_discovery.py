# pylint: disable=protected-access
from asyncio import sleep
from unittest.mock import patch

import pytest
from pytest import raises

from pizone import Controller, Listener, discovery
from pizone.discovery import DiscoveryService


@pytest.mark.asyncio
@patch.object(DiscoveryService, "_get_broadcasts")
async def test_broadcast(broadcasts):
    broadcasts.return_value = []

    async with discovery():
        assert broadcasts.called


@pytest.mark.asyncio
@patch.object(DiscoveryService, "_send_broadcasts")
async def test_messages_sent(send_broadcasts):
    async with discovery():
        assert send_broadcasts.called


@pytest.mark.asyncio
@patch.object(DiscoveryService, "_send_broadcasts")
async def test_rescan(send):
    async with discovery() as service:
        assert not service.is_closed
        assert send.call_count == 1

        await service._rescan()
        await sleep(0)
        assert send.call_count == 2

    assert service.is_closed


@pytest.mark.asyncio
async def test_fail_on_connect(caplog):
    from .conftest import MockDiscoveryService

    async def start_discovery_noop():
        pass

    service = MockDiscoveryService()
    service._start_discovery = start_discovery_noop
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


@pytest.mark.asyncio
async def test_connection_lost(service, caplog):
    service._on_connection_lost(IOError("Nonspecific"))
    await sleep(0)

    assert len(caplog.messages) == 1
    assert caplog.messages[0] == "Connection Lost unexpectedly: OSError('Nonspecific')"

    assert service.is_closed


@pytest.mark.asyncio
async def test_discovery(service):
    assert len(service._controllers) == 1
    assert "000000001" in service._controllers

    controller = service._controllers["000000001"]  # type: Controller
    assert controller.device_uid == "000000001"
    assert controller.device_ip == "8.8.8.8"
    assert controller.mode == Controller.Mode.HEAT

    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[0] == ("SystemMODE", {"SystemMODE": "cool"})
    assert controller.mode == Controller.Mode.COOL


@pytest.mark.asyncio
async def test_legacy_discovery(legacy_service):
    service = legacy_service

    assert len(service._controllers) == 1
    assert "000000001" in service._controllers

    controller = service._controllers["000000001"]  # type: Controller
    assert controller.device_uid == "000000001"
    assert controller.device_ip == "8.8.8.8"
    assert controller.mode == Controller.Mode.HEAT

    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[0] == ("SystemMODE", {"SystemMODE": "cool"})
    assert controller.mode == Controller.Mode.COOL


@pytest.mark.asyncio
async def test_ip_addr_change(service):
    """Verify that IP address changes are handled."""
    controller = service._controllers["000000001"]  # type: ignore[attr-defined]  # type: Controller
    assert controller.device_uid == "000000001"
    assert controller.device_ip == "8.8.8.8"

    service._process_datagram(
        b"ASPort_12107,Mac_000000001,IP_8.8.8.4,iZone,iLight,iDrate", ("8.8.8.4", 12107)
    )
    await sleep(0)

    assert controller.device_ip == "8.8.8.4"


@pytest.mark.asyncio
async def test_reconnect(service, caplog):
    controller = service._controllers["000000001"]  # type: Controller
    assert controller.device_uid == "000000001"
    assert controller.mode == Controller.Mode.HEAT

    controller._failed_connection(ConnectionError("Fake connection error"))
    with raises(ConnectionError):
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
    assert controller.sent[0] == ("SystemMODE", {"SystemMODE": "cool"})


@pytest.mark.asyncio
async def test_reconnect_listener(service):
    controller = service._controllers["000000001"]  # type: Controller

    calls = []

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
    with raises(ConnectionError):
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
    controller2 = service._controllers["000000002"]  # type: Controller

    assert len(calls) == 4
    assert calls[-1] == ("discovered", controller2)

    service.remove_listener(listener)

    controller._failed_connection(ConnectionError("Fake connection error"))
    with raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_rescan_cooldown_suppression(service):
    """Verify that rescan is suppressed within the cool-down window."""
    from unittest.mock import AsyncMock

    original_rescan = service._rescan
    service._rescan = AsyncMock(side_effect=original_rescan)

    # First fetch_controllers with timeout should trigger rescan
    await service.fetch_controllers(timeout=0.1)
    assert service._rescan.call_count == 1

    # Immediate second fetch within cool-down should not trigger new rescan
    await service.fetch_controllers(timeout=0.1)
    assert service._rescan.call_count == 1  # Still 1, not 2


@pytest.mark.asyncio
async def test_fetch_controller_already_known(service):
    """Verify that fetch_controller returns immediately for known controller."""
    controller = await service.fetch_controller("000000001", timeout=1.0)
    assert controller is not None
    assert controller.device_uid == "000000001"


@pytest.mark.asyncio
async def test_fetch_controller_unknown_no_timeout(service):
    """Verify that fetch_controller returns None for unknown controller without timeout."""
    controller = await service.fetch_controller("unknown_uid")
    assert controller is None


@pytest.mark.asyncio
async def test_fetch_controller_unknown_timeout_expires(service):
    """Verify that fetch_controller returns None when timeout expires."""
    controller = await service.fetch_controller("unknown_uid", timeout=0.1)
    assert controller is None


@pytest.mark.asyncio
async def test_fetch_controllers_no_timeout(service):
    """Verify that fetch_controllers returns snapshot without timeout."""
    controllers = await service.fetch_controllers()
    assert len(controllers) == 1
    assert "000000001" in controllers


@pytest.mark.asyncio
async def test_fetch_controllers_with_timeout(service):
    """Verify that fetch_controllers waits when timeout is specified."""
    controllers = await service.fetch_controllers(timeout=0.1)
    assert len(controllers) == 1
    assert "000000001" in controllers


@pytest.mark.asyncio
async def test_listener_controller_discovered_on_add(service):
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
