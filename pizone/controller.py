
import socket
import requests
import json

import pizone.discovery as discovery
from pizone.utils import CoolDown


REQUEST_TIMEOUT = 0.5

SYS_ON = 'on'
SYS_OFF = 'off'
SYS_DISABLED = 'disabled'

MODE_COOL = 'cool'
MODE_HEAT = 'heat'
MODE_VENT = 'vent'
MODE_DRY = 'dry'
MODE_AUTO = 'auto'
MODES_VALID = [MODE_COOL, MODE_HEAT, MODE_VENT, MODE_DRY, MODE_AUTO]

FAN_LOW = 'low'
FAN_MED = 'med'
FAN_HIGH = 'high'
FAN_AUTO = 'auto'

_VALID_FAN_MODES = {
    'disabled' : [ FAN_LOW, FAN_MED, FAN_HIGH ],
    '3-speed' : [ FAN_LOW, FAN_MED, FAN_HIGH, FAN_AUTO ],
    '2-speed' : [ FAN_LOW, FAN_HIGH, FAN_AUTO ],
    'var-speed' : [ FAN_LOW, FAN_MED, FAN_HIGH, FAN_AUTO ],
    }


class Controller(object):
    """Interface to IZone controller"""

    def __init__(self, id = None):
        if id is None:
            allDisco = discovery.get_all()
            if len(allDisco) > 1:
                raise ConnectionAbortedError('Multiple iZone instances discovered on network')
            if len(allDisco) == 0:
                raise ConnectionRefusedError('No iZone device found')

            disco = allDisco[0]
        else:
            try:
                socket.inet_aton(id)
                self._ip = id  # id is an IP
            except socket.error:
                self._ip = None

            if self._ip is None:
                # id is a common name, try discovery
                disco = discovery.get_ID(id)
                if disco is None:
                    raise ConnectionRefusedError("no device found for %s" % id)

        if disco is not None:
            self._ip = disco['ip']
            self._deviceUId = disco['id']

        self.info = {}
        self.zones = []
        self.fan_modes = []

        self._deviceUId = None

        self.refresh_all(force=True)

    def refresh_all(self, force=False):
        self.refresh_info(force)
        self.refresh_zones(force)
        self.refresh_schedules(force)

    @CoolDown(10000)
    def refresh_info(self, force=False, retry=True):
        values = self._SystemSettings = self._get_resource('SystemSettings')

        uid = values['AirStreamDeviceUId']
        if self._deviceUId is None:
            self._deviceUId = uid
        elif self._deviceUId != uid:
            if not retry:
                raise ConnectionAbortedError("iZone device has changed it's UID")
            # if the UID we get back is different, try reconnecting and retrying
            _refresh_ip()
            return self.refresh_info(retry=False)

        self.fan_modes = _VALID_FAN_MODES[values['FanAuto']]

    @CoolDown(10000)
    def refresh_zones(self, force=False):
        from pizone.zone import Zone
        zones = self.noOfZones - self.noOfConst
        
        zoneDataPart = self._get_resource('Zones1_4')
        if zones > 4:
            zoneDataPart.extend(self._get_resource('Zones5_8'))
            if zones > 8:
                zoneDataPart.extend(self._get_resource('Zones9_12'))

        for i in range(0, zones):
            zoneData = zoneDataPart[i]
            if len(self.zones) <= i:
                self.zones.append(Zone(self, zoneData))
            else:
                self.zones[i].update_zone(zoneData)

    @CoolDown(10000)
    def refresh_schedules(self, force=False):
        pass

    @property 
    def ip(self):
        return self._ip

    @property
    def deviceUId(self):
        return self._deviceUId

    @property
    def sysOn(self):
        return _get_system_state('SysOn') == SYS_ON

    @sysOn.setter
    def sysOn(self, value):
        self._set_system_state('SysOn', 'SystemON', SYS_ON if value else SYS_OFF)

    @property
    def sysMode(self):
        return _get_system_state('SysMode')

    @sysMode.setter
    def sysMode(self, value):
        if value not in MODES_VALID:
            raise AttributeError('Invalid Mode \"{}\"'.format(value))
        self._set_system_state('SysMode', 'SystemMODE', value)

    @property
    def sysFan(self):
        return self._get_system_state('SysFan')

    @sysFan.setter
    def sysFan(self, value):
        if value not in self.fan_modes:
            raise AttributeError('Invalid Fan Mode \"{}\"'.format(value))
        self._set_system_state('SysFan', 'SystemFAN', value, 'medium' if value == 'med' else value)

    @property
    def sleepTimer(self, value):
        return int(self._get_system_state('SleepTimer'))

    @sleepTimer.setter
    def sleepTimer(self, value):
        time = int(value)
        if time < 0 or time > 120 or time % 30 != 0:
            raise AttributeError('Invalid Sleep Timer \"{}\", must be divisible by 30'.format(value))
        self._set_system_state('SleepTimer', 'SleepTimer', value, time)

    @property
    def freeAir(self):
        return self._get_system_state('FreeAir')

    @freeAir.setter
    def freeAir(self, value):
        if self.freeAir == SYS_DISABLED:
            raise AttributeError('Free air is disabled')
        self._set_system_state('FreeAir', 'FreeAir', SYS_ON if value else SYS_OFF)

    @property
    def supply(self):
        return self._get_system_state('Supply')

    @property
    def setPoint(self):
        return self._get_system_state('Setpoint')

    @property
    def temp(self):
        return self._get_system_state('Temp')

    @property
    def ecoLock(self):
        return self._get_system_state('EcoLock') == 'true'

    @property
    def ecoMin(self):
        return float(self._get_system_state('EcoMin')) if self.ecoLock else 15.0

    @property
    def ecoMax(self):
        return float(self._get_system_state('EcoMax')) if self.ecoLock else 30.0

    @setPoint.setter
    def setPoint(self, value):
        point = float(value)
        if point % 0.5 != 0:
            raise AttributeError('SetPoint \'{}\' not rounded to nearest 0.5'.format(point))
        if point < self.ecoMin or point > self.ecoMax:
            raise AttributeError('SetPoint \'{}\' is out of range'.format(point))
        self._set_system_state('Setpoint', 'UnitSetpoint', value, str(value))

    @property
    def rasMode(self):
        return self.self._get_system_state('RAS')

    @property
    def ctrlZone(self):
        return self.self._get_system_state('CtrlZone')

    @property
    def noOfZones(self):
        return self._get_system_state('NoOfZones')

    @property
    def noOfConst(self):
        return self._get_system_state('NoOfConst')



    def _get_system_state(self, state):
        self.refresh_info()
        return self._SystemSettings[state]

    def _set_system_state(self, state, command, value, sendValue=None):
        if sendValue is None:
            sendValue = value
        if self._SystemSettings[state] == value:
            return
        self._send_command(command, sendValue)
        self._SystemSettings[state] = value

    def _refresh_ip(self):
        if self._deviceUId is None:
            raise ConnectionRefusedError("Attempted to reconnect IP but no UID set")
        disco = discovery.get_ID(self.deviceUId)
        if disco is None:
            raise ConnectionResetError("Lost connection to the iZone device")
        self._ip = disco['ip']

    def _get_resource(self, resource, retry=True):
        responseJson = None
        with requests.session() as session:
            try:
                r = session.get('http://%s/%s' % (self.ip, resource), timeout=0.5)
                r.raise_for_status()
                responseJson = r.json()
            except requests.exceptions.RequestException:
                # Attempt to reconnect to a different IP using netdisco
                if not retry:
                    raise ConnectionResetError("Lost connection to the iZone device")
                self._refresh_ip()
                return self._get_resource(resource, retry=False)

        return responseJson

    def _send_command(self, command, data, retry=True):
        with requests.session() as session:
            try:
                data = { command : data }
                r = session.post('http://%s/%s' % (self.ip, command), timeout=3, data=json.dumps(data))
                r.raise_for_status()
            except requests.exceptions.RequestException as exc:
                # Attempt to reconnect to a different IP using netdisco
                if not retry:
                    raise ConnectionResetError("Lost connection to the iZone device") from exc
                self._refresh_ip()
                return self._send_command(command, data, retry=False)


