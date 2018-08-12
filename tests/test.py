
from pizone import Controller

try:
    # Initialize the controler by creating it.
    # Can also be passed the IP address directly, or the controller UId as a string (mine is '000013170')
    # Will follow the controller through IP address changes
    cont = Controller()

    print(cont.ip)
    print(cont.deviceUId)
    print(cont.supplyTemp)

    print(cont._SystemSettings)
    for zo in cont.zones:
        print(zo._zoneData)



except IOError as ex:
    print('exception:')
    print(ex)