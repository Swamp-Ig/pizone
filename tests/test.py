"""Test for controller"""

from pizone import Controller

try:
    # Initialize the controler by creating it.
    # Can also be passed the IP address directly,
    # or the controller UId as a string (mine is '000013170')
    # Will follow the controller through IP address changes
    CTRL = Controller()

    print(CTRL.device_ip)
    print(CTRL.device_uid)
    print(CTRL.temp_supply)

    #pylint: disable=protected-access
    print(CTRL._system_settings)
    for zo in CTRL.zones:
        print(zo._zone_data)



except IOError as ex:
    print('exception:')
    print(ex)
