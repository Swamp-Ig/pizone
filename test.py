
import asyncio
import pizone.controller as controller
import pizone.zone as zone

try:
    cont = controller.Controller()

    print(cont.ip)
    print(cont.deviceUId)

    print(cont._SystemSettings)
    for zo in cont.zones:
        print(zo._zoneData)

except IOError as ex:
    print('exception:')
    print(ex)