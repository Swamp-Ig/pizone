
from asyncio import sleep

from asynctest.mock import patch

from pizone import discovery, Controller, Listener
from pizone.discovery import DiscoveryService

from pytest import raises


@patch.object(DiscoveryService, '_get_broadcasts')
async def test_broadcast(broadcasts):
    broadcasts.return_value = []

    async with discovery():
        assert broadcasts.called


@patch.object(DiscoveryService, '_send_broadcasts')
async def test_messages_sent(send_broadcasts):
    async with discovery():
        assert send_broadcasts.called


@patch.object(DiscoveryService, '_send_broadcasts')
async def test_rescan(send):
    async with discovery() as service:
        assert not service.is_closed
        assert send.call_count == 1

        await service.rescan()
        await sleep(0)
        assert send.call_count == 2

    assert service.is_closed


async def test_fail_on_connect(loop, caplog):
    from .conftest import MockDiscoveryService

    service = MockDiscoveryService(loop)
    service.connected = False

    async with service:
        service._process_datagram(
            b'ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate',
            ('8.8.8.8', 12107))
        sleep(0)

    assert len(caplog.messages) == 1
    assert caplog.messages[0][:41] == \
        "Can't connect to discovered server at IP "
    assert not service.controllers


async def test_connection_lost(service, caplog):
    service.connection_lost(IOError("Nonspecific"))
    await sleep(0)

    assert len(caplog.messages) == 1
    assert caplog.messages[0] == \
        "Connection Lost unexpectedly: OSError('Nonspecific',)"

    assert service.is_closed


async def test_discovery(service):
    assert len(service.controllers) == 1
    assert '000000001' in service.controllers

    controller = service.controllers['000000001']  # type: Controller
    assert controller.device_uid == '000000001'
    assert controller.device_ip == '8.8.8.8'
    assert controller.mode == Controller.Mode.HEAT

    # Not updated yet
    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[0] == ('SystemMODE', 'cool')
    assert controller.mode == Controller.Mode.HEAT

    # Now updated
    await controller.change_system_state('SysMode', 'cool')
    assert controller.mode == Controller.Mode.COOL


async def test_ip_addr_change(service, caplog):
    controller = service.controllers['000000001']  # type: Controller
    assert controller.device_uid == '000000001'
    assert controller.device_ip == '8.8.8.8'

    service._process_datagram(
        b'ASPort_12107,Mac_000000001,IP_8.8.8.4,iZone,iLight,iDrate',
        ('8.8.8.4', 12107))
    sleep(0)

    assert controller.device_ip == '8.8.8.4'


async def test_reconnect(service, caplog):
    controller = service.controllers['000000001']  # type: Controller
    assert controller.device_uid == '000000001'
    assert controller.mode == Controller.Mode.HEAT

    controller.connected = False
    with raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert caplog.messages[0][:30] == \
        "Connection to controller lost:"
    assert not controller.sent

    controller.connected = True
    service._process_datagram(
        b'ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate',
        ('8.8.8.8', 12107))

    await sleep(0.1)

    # Reconnect OK
    assert caplog.messages[1][:23] == \
        "Controller reconnected:"
    await controller.set_mode(Controller.Mode.COOL)
    assert controller.sent[0] == ('SystemMODE', 'cool')


async def test_reconnect_listener(service):
    controller = service.controllers['000000001']  # type: Controller

    calls = []

    class TestListener(Listener):
        def controller_discovered(self, ctrl: Controller) -> None:
            calls.append(('discovered', ctrl))

        def controller_disconnected(
                self, ctrl: Controller, ex: Exception) -> None:
            calls.append(('disconnected', ctrl, ex))

        def controller_reconnected(self, ctrl: Controller) -> None:
            calls.append(('reconnected', ctrl))
    listener = TestListener()

    service.add_listener(listener)
    await sleep(0)

    assert len(calls) == 1
    assert calls[-1] == ('discovered', controller)

    controller.connected = False
    with raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert len(calls) == 2
    assert calls[-1][0:2] == ('disconnected', controller)

    controller.connected = True
    service._process_datagram(
        b'ASPort_12107,Mac_000000001,IP_8.8.8.8,iZone,iLight,iDrate',
        ('8.8.8.8', 12107))
    await sleep(0.1)

    assert len(calls) == 3
    assert calls[-1] == ('reconnected', controller)

    service._process_datagram(
        b'ASPort_12107,Mac_000000002,IP_8.8.8.4,iZone,iLight,iDrate',
        ('8.8.8.8', 12107))
    await sleep(0.1)
    controller2 = service.controllers['000000002']  # type: Controller

    assert len(calls) == 4
    assert calls[-1] == ('discovered', controller2)

    service.remove_listener(listener)

    controller.connected = False
    with raises(ConnectionError):
        await controller.set_mode(Controller.Mode.COOL)

    assert len(calls) == 4
