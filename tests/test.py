"""Test for controller"""

from pizone import controller_single, Controller

try:
    # Initialize the controler by creating it.
    # Can also be passed the IP address directly,
    # or the controller UId as a string (mine is '000013170')
    # Will follow the controller through IP address changes
    CTRL = controller_single()

    print(CTRL.device_ip)
    print(CTRL.device_uid)
    print(CTRL.temp_supply)

    #pylint: disable=protected-access
    print(CTRL._system_settings)
    for zo in CTRL.zones:
        print(zo._zone_data)

    CTRL.mode = Controller.Mode.HEAT
    CTRL.state = False

    # Monkey with the guts. This should force an IP renew
    CTRL._ip = 'monkey'
    CTRL.refresh_system(force=True)

except IOError as ex:
    print('exception:')
    print(ex)
