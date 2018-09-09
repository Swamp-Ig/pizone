"""Test for controller"""

import asyncio
from asyncio import Condition
import unittest
from typing import Optional

from aiounittest import AsyncTestCase

from pizone import Controller, Listener, Zone, discovery

# pylint: disable=missing-docstring

class TestListener(Listener, AsyncTestCase):
    """Test Listener"""

    async def notify(self):
        assert self.condition
        async with self.condition:
            self.condition.notify()

    def controller_discovered(self, ctrl):
        self.ctrl = ctrl
        self.loop.create_task(self.notify())

    def controller_reconnected(self, ctrl):
        self.loop.create_task(self.notify())

    def setUp(self):
        "Hook method for setting up the test fixture before exercising it."
        self.loop = None
        self.condition: Optional[Condition] = None
        self.ctrl = None

    def dump_data(self):
        """Testing"""
        ctrl = self.ctrl
        print(ctrl.device_ip)
        print(ctrl.device_uid)
        print(f"supply={ctrl.temp_supply} mode={ctrl.mode} isOn={ctrl.state}")
        print(f"sleep_timer={ctrl.sleep_timer}")

        for zone in ctrl.zones:
            zone_target = zone.temp_setpoint if zone.mode == Zone.Mode.AUTO else zone.mode.value
            print(f"Name {zone.name} type:{zone.type.value} temp:{zone.temp_current} " +
                  f"target:{zone_target}")

    async def test_async(self):
        self.loop = asyncio.get_event_loop()
        self.condition = Condition(loop=self.loop)

        async with discovery(self):
            async with self.condition:
                await self.condition.wait()

            self.dump_data()

            # test setting values
            await self.ctrl.set_mode(Controller.Mode.COOL)

            Controller.CONNECT_RETRY_TIMEOUT = 2

            self.ctrl._ip = 'bababa' # pylint: disable=protected-access
            try:
                await self.ctrl.set_sleep_timer(30)
                assert False, "Should be unreachable code"
            except ConnectionError:
                pass

            # Should reconnect here
            async with self.condition:
                await self.condition.wait()

            await self.ctrl.set_mode(Controller.Mode.HEAT)

            self.dump_data()

if __name__ == '__main__':
    unittest.main()
