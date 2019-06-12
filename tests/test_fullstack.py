"""Test for controller"""

from asyncio import Event
from pizone import Controller, Listener, Zone, discovery
from pytest import raises, mark


def dump_data(ctrl):
    """Testing"""
    print(ctrl.device_ip)
    print(ctrl.device_uid)
    print("supply={0} mode={1} isOn={2}".format(
        ctrl.temp_supply, ctrl.mode, ctrl.is_on))
    print("sleep_timer={0}".format(ctrl.sleep_timer))

    for zone in ctrl.zones:
        zone_target = (
            zone.temp_setpoint
            if zone.mode == Zone.Mode.AUTO
            else zone.mode.value)
        print("Name {0} type:{1} temp:{2} target:{3}".format(
            zone.name, zone.type.value, zone.temp_current, zone_target))


# Disabled because this will only work at my house
@mark.skip
async def test_full_stack(loop):
    controllers = []
    event = Event()

    class TestListener(Listener):
        def controller_discovered(self, _ctrl):
            controllers.append(_ctrl)
            event.set()

        def controller_reconnected(self, ctrl):
            event.set()
    listener = TestListener()

    async with discovery(listener):
        await event.wait()
        event.clear()

        ctrl = controllers[0]

        dump_data(ctrl)

        # test setting values
        await ctrl.set_mode(Controller.Mode.AUTO)

        Controller.CONNECT_RETRY_TIMEOUT = 2

        ctrl._ip = 'bababa'  # pylint: disable=protected-access
        with raises(ConnectionError):
            await ctrl.set_sleep_timer(30)

        # Should reconnect here
        await event.wait()
        event.clear()

        await ctrl.set_mode(Controller.Mode.HEAT)

        dump_data(ctrl)
