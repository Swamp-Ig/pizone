"""Test for controller"""

import asyncio
from asyncio import Task, AbstractEventLoop

from pizone import Controller, Zone, discovery

def discovered(controller: Controller) -> None:
    """Testing"""
    print(controller.device_ip)
    print(controller.device_uid)
    print(f"supply={controller.temp_supply} mode={controller.mode} isOn={controller.state}")
    print(f"sleep_timer={controller.sleep_timer}")

    for zone in controller.zones:
        print(f"Name {zone.name} type:{zone.type.value} temp:{zone.temp_current} " +
              f"target:{zone.temp_setpoint if zone.mode == Zone.Mode.AUTO else zone.mode.value}")

    #controller.mode = Controller.Mode.HEAT
    #controller.state = not controller.state
    #print(f"supply={controller.temp_supply} mode={controller.mode} isOn={controller.state}")

    Controller.CONNECT_RETRY_TIMEOUT = 2

    controller._ip = 'bababa' # pylint: disable=protected-access
    try:
        controller.sleep_timer = 30
    except ConnectionError:
        pass

    controller.add_listener(reconnected)

# Callback for when reconnected
def reconnected(controller: Controller) -> None:
    """Testing"""
    controller.sleep_timer = 30
    controller.remove_listener(reconnected)

    print("Successfully reconnected")
    controller.sleep_timer = 0


    task.cancel()

loop: AbstractEventLoop = asyncio.get_event_loop()

task: Task = loop.create_task(discovery(discovered))

loop.run_until_complete(task)

loop.run_until_complete(asyncio.sleep(600))
