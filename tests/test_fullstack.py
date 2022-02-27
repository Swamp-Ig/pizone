"""Test for controller"""

from asyncio import Event, TimeoutError, wait_for

from pizone import Controller, Listener, Zone, discovery
from pytest import fail, raises


class ListenerTesting(Listener):
    def __init__(self) -> None:
        self._controller = None
        self._connected = Event()
        self._updated = Event()
        self.connect_count = 0
        self.update_count = 0

    def controller_discovered(self, _ctrl):
        if self._controller is not None:
            return
        self._controller = _ctrl
        self._connected.set()
        self.connect_count += 1

    def controller_disconnected(self, ctrl, ex):
        if self._controller is not ctrl:
            return
        self._connected.clear()

    def controller_reconnected(self, ctrl):
        if self._controller is not ctrl:
            return
        self._connected.set()
        self.connect_count += 1

    def controller_update(self, ctrl: Controller) -> None:
        if self._controller is not ctrl:
            return
        self.update_count += 1
        self._updated.set()

    async def await_controller(self):
        await wait_for(self._connected.wait(), 5)
        return self._controller

    async def await_update(self):
        self._updated.clear()
        await wait_for(self._updated.wait(), 10)


def dump_data(ctrl):
    """Testing"""
    print(ctrl.device_ip)
    print(ctrl.device_uid)
    print(
        "supply={0} mode={1} isOn={2}".format(ctrl.temp_supply, ctrl.mode, ctrl.is_on)
    )
    print("sleep_timer={0}".format(ctrl.sleep_timer))

    for zone in ctrl.zones:
        zone_target = (
            zone.temp_setpoint if zone.mode == Zone.Mode.AUTO else zone.mode.value
        )
        print(
            (
                "Name {0} type:{1} temp:{2} target:{3} "
                + "airflow_min:{4} airflow_max:{5}"
            ).format(
                zone.name,
                zone.type.value,
                zone.temp_current,
                zone_target,
                zone.airflow_min,
                zone.airflow_max,
            )
        )


async def test_full_stack():
    listener = ListenerTesting()

    async with discovery(listener):
        ctrl = await listener.await_controller()

        dump_data(ctrl)

        old_mode = ctrl.mode
        old_airflow_min = ctrl.zones[1].airflow_min
        old_airflow_max = ctrl.zones[1].airflow_max

        try:
            # test setting values
            mode = (
                Controller.Mode.COOL
                if old_mode == Controller.Mode.AUTO
                else Controller.Mode.AUTO
            )
            await ctrl.set_mode(mode)
            assert ctrl.mode == mode

            # test set airflow min
            nmin = 20 if old_airflow_min == 10 else 10
            await ctrl.zones[1].set_airflow_min(nmin)

            with raises(AttributeError):
                await ctrl.zones[1].set_airflow_min(41)

            with raises(AttributeError):
                await ctrl.zones[1].set_airflow_min(-1)

            with raises(AttributeError):
                await ctrl.zones[1].set_airflow_min(105)

            assert ctrl.zones[1].airflow_min == nmin

            # test set airflow max
            nmax = 80 if old_airflow_max == 90 else 90
            await ctrl.zones[1].set_airflow_max(nmax)

            with raises(AttributeError):
                await ctrl.zones[1].set_airflow_max(41)

            with raises(AttributeError):
                await ctrl.zones[1].set_airflow_max(-1)

            with raises(AttributeError):
                await ctrl.zones[1].set_airflow_max(105)

            assert ctrl.zones[1].airflow_max == nmax

            # Wait for a re-read from the server
            old_count = listener.update_count
            await listener.await_update()
            assert listener.update_count > old_count

            assert ctrl.mode == mode
            assert ctrl.zones[1].airflow_min == nmin
            assert ctrl.zones[1].airflow_max == nmax

        finally:
            # Tidy everything up
            await ctrl.set_mode(old_mode)
            await ctrl.zones[1].set_airflow_min(old_airflow_min)
            await ctrl.zones[1].set_airflow_max(old_airflow_max)

        dump_data(ctrl)


async def test_reconnect():
    listener = ListenerTesting()

    async with discovery(listener):
        ctrl = await listener.await_controller()

        assert listener.connect_count == 1

        # test automatic reconnection
        Controller.CONNECT_RETRY_TIMEOUT = 2

        ctrl._ip = "bababa"  # pylint: disable=protected-access
        with raises(ConnectionError):
            await ctrl.set_sleep_timer(30)

        # Should reconnect here
        await listener.await_controller()

        assert listener.connect_count == 2

        await ctrl.set_sleep_timer(0)


async def test_power():

    listener = ListenerTesting()

    async with discovery(listener):
        ctrl = await listener.await_controller()

        result = ctrl.power

        result = await ctrl._send_command_async(
            "iZoneRequestV2", {"iZoneV2Request":{"Type": 2, "No": 0, "No1": 0}}
        )
        import json
        result = json.loads(result)

        dump_data(ctrl)
