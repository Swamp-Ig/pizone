
import socket
import json
import requests
from enum import Enum
from typing import (Union, NoReturn)

import pizone.discovery as discovery
from pizone.utils import CoolDown

class Controller:
    """Interface to IZone controller"""

    class Mode(Enum):
        """Valid controller modes"""
        COOL = 'cool'
        HEAT = 'heat'
        VENT = 'vent'
        DRY = 'dry'
        AUTO = 'auto'

    class Fan(Enum):
        """All fan modes"""
        LOW = 'low'
        MED = 'med'
        HIGH = 'high'
        AUTO = 'auto'

    REQUEST_TIMEOUT = 0.5

    _VALID_FAN_MODES = {
        'disabled' : [ Fan.LOW, Fan.MED, Fan.HIGH ],
        '3-speed' : [ Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO ],
        '2-speed' : [ Fan.LOW, Fan.HIGH, Fan.AUTO ],
        'var-speed' : [ Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO ],
        }

    def __init__(self, id:str = None):
        """Create a controller

        Args:
            id: Accepts an IP address, or controller UId as a string (eg: mine is '000013170')

        Raises:
            ConnectionAbortedError: If id is not set and more than one iZone instance is discovered on the network.
            ConnectionRefusedError: If no iZone discovered, or no iZone device discovered at the given IP address or UId
        """
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

    def refresh_all(self, force:bool=False) -> None:
        """Refresh all controller information"""
        self.refresh_system(force)
        self.refresh_zones(force)
        self.refresh_schedules(force)

    @CoolDown(10000)
    def refresh_system(self, force:bool=False) -> None:
        """Refresh the system settings. 

        This has a cool-down of 10 seconds, and will only refresh if at least this time period has elapsed.

        Args:
            force: If true, force an update ignoring the cool-down.
        """
        self._refresh_system(True)

    def _refresh_system(self, retry):
        values = self._SystemSettings = self._get_resource('SystemSettings')

        uid = values['AirStreamDeviceUId']
        if self._deviceUId is None:
            self._deviceUId = uid
        elif self._deviceUId != uid:
            if not retry:
                raise ConnectionAbortedError("iZone device has changed it's UID")
            # if the UID we get back is different, try reconnecting and retrying
            _refresh_ip()
            return self._refresh_info(retry=False)

        self.fan_modes = Controller._VALID_FAN_MODES[values['FanAuto']]

    @CoolDown(10000)
    def refresh_zones(self, force:bool=False) -> None:
        """Refresh the Zone information. 

        This has a cool-down of 10 seconds, and will only refresh if at least this time period has elapsed.

        Args:
            force: If true, force an update ignoring the cool-down.
        """

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
    def refresh_schedules(self, force:bool=False) -> None:
        """TODO: Not implemented"""
        pass

    @property 
    def ip(self) -> str:
        """IP Address of the unit"""
        return self._ip

    @property
    def deviceUId(self) -> str:
        '''UId of the unit'''
        return self._deviceUId

    @property
    def sysOn(self) -> bool:
        """True if the system is turned on"""
        return _get_system_state('SysOn') == 'on'

    @sysOn.setter
    def sysOn(self, value:bool) -> None:
        self._set_system_state('SysOn', 'SystemON', 'on' if value else 'off')

    @property
    def sysMode(self) -> Mode:
        return Controller.Mode(_get_system_state('SysMode'))

    @sysMode.setter
    def sysMode(self, value:Mode):
        self._set_system_state('SysMode', 'SystemMODE', value.value)

    @property
    def sysFan(self) -> Fan:
        return Controller.Fan(self._get_system_state('SysFan'))

    @sysFan.setter
    def sysFan(self, value:Fan) -> None:
        self._set_system_state('SysFan', 'SystemFAN', value.value, 'medium' if value is Controller.Fan.MED else value.value)

    @property
    def sleepTimer(self) -> int:
        """The sleep timer. 
        
        Valid settings are 0, 30, 60, 90, 120
        
        Raises:
            AttributeError: On setting if the argument value is not valid
        """
        return int(self._get_system_state('SleepTimer'))

    @sleepTimer.setter
    def sleepTimer(self, value:int):
        time = int(value)
        if time < 0 or time > 120 or time % 30 != 0:
            raise AttributeError('Invalid Sleep Timer \"{}\", must be divisible by 30'.format(value))
        self._set_system_state('SleepTimer', 'SleepTimer', value, time)

    @property
    def freeAirEnabled(self) -> bool:
        """Test if the system has free air system available"""
        return self._get_system_state('FreeAir') == 'disabled'

    @property
    def freeAir(self) -> bool:
        """True if the free air system is turned on. False if unavailable or off
        
        Raises:
            AttributeError: If attempting to set the state of the free air system when it is not available.        
        """
        return self._get_system_state('FreeAir') == 'on'

    @freeAir.setter
    def freeAir(self, value:bool) -> None:
        if not self.freeAirEnabled:
            raise AttributeError('Free air is disabled')
        self._set_system_state('FreeAir', 'FreeAir', 'on' if value else 'off')

    @property
    def supplyTemp(self) -> float:
        """Current supply, or in duct, air temperature."""
        return float(self._get_system_state('Supply'))

    @property
    def setPoint(self) -> float:
        """AC unit setpoint temperature.

        This is the unit target temp with with rasMode == RAS, or with rasMode == master and ctrlZone == 13.
        
        Args:
            value: Valid settings are between ecoMin and ecoMax, at 0.5 degree units.
        
        Raises:
            AttributeError: On setting if the argument value is not valid. Can still be set even if the mode isn't appropriate.
        """
        return float(self._get_system_state('Setpoint'))

    @property
    def returnTemp(self) -> float:
        """The return, or room, air temperature"""
        return float(self._get_system_state('Temp'))

    @property
    def ecoLock(self) -> bool:
        """True if eco lock setting is on."""
        return self._get_system_state('EcoLock') == 'true'

    @property
    def ecoMin(self) -> float:
        """The value for the eco lock minimum, or 15 if eco lock not set"""
        return float(self._get_system_state('EcoMin')) if self.ecoLock else 15.0

    @property
    def ecoMax(self) -> float:
        """The value for the eco lock maxium, or 30 if eco lock not set"""
        return float(self._get_system_state('EcoMax')) if self.ecoLock else 30.0

    @setPoint.setter
    def setPoint(self, value:float) -> None:
        if value % 0.5 != 0:
            raise AttributeError('SetPoint \'{}\' not rounded to nearest 0.5'.format(point))
        if value < self.ecoMin or value > self.ecoMax:
            raise AttributeError('SetPoint \'{}\' is out of range'.format(value))
        self._set_system_state('Setpoint', 'UnitSetpoint', value, str(value))

    @property
    def rasMode(self) -> str:
        """This indicates the current selection of the Return Air temperature Sensor. Possible values are:
        master: the AC unit is controlled from a CTS, which is manually selected
        RAS:    the AC unit is controller from its own return air sensor
        zones:  the AC unit is controlled from a CTS, which is automatically selected dependant on the cooling/ heating need of zones.
        """
        return Controller.RASMode(self.self._get_system_state('RAS'))

    @property
    def ctrlZone(self) -> int:
        """This indicates the zone that currently controls the AC unit. Value interpreted in combination with rasMode"""
        return int(self.self._get_system_state('CtrlZone'))

    @property
    def noOfZones(self) -> int:
        """This indicates the number of zones the system is configured for."""
        return int(self._get_system_state('NoOfZones'))

    @property
    def noOfConst(self) -> int:
        """This indicates the number of constant zones the system is configured for."""
        return self._get_system_state('NoOfConst')

    @property
    def sysType(self) -> str:
        """This indicates the type of the iZone system connected. Possible values are:
        110: the system is zone control only and all the zones are OPEN/CLOSE zones
        210: the system is zone control only. Zones can be temperature controlled, dependant on the zone settings.
        310: the system is zone control and unit control.
        """
        return self._get_system_state('SysType')


    def _get_system_state(self, state):
        self.refresh_system()
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
                r = session.get('http://%s/%s' % (self.ip, resource), timeout=Controller.REQUEST_TIMEOUT)
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


