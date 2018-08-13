"""Controller module"""

import socket
import json

from enum import Enum

from pizone.utils import CoolDown

class ControllerMode(Enum):
    """Valid controller modes"""
    COOL = 'cool'
    HEAT = 'heat'
    VENT = 'vent'
    DRY = 'dry'
    AUTO = 'auto'

class ControllerFan(Enum):
    """All fan modes"""
    LOW = 'low'
    MED = 'med'
    HIGH = 'high'
    AUTO = 'auto'

class Controller:
    """Interface to IZone controller"""

    Mode = ControllerMode
    Fan = ControllerFan

    REQUEST_TIMEOUT = 0.5

    _VALID_FAN_MODES = {
        'disabled' : [Fan.LOW, Fan.MED, Fan.HIGH],
        '3-speed' : [Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO],
        '2-speed' : [Fan.LOW, Fan.HIGH, Fan.AUTO],
        'var-speed' : [Fan.LOW, Fan.MED, Fan.HIGH, Fan.AUTO],
        }

    def __init__(self, ipOrUId: str = None):
        """Create a controller

        Args:
            id: Accepts an IP address, or controller UId as a string (eg: mine is '000013170')

        Raises:
            ConnectionAbortedError: If id is not set and more than one iZone instance is
                discovered on the network.
            ConnectionRefusedError: If no iZone discovered, or no iZone device discovered at
            the given IP address or UId
        """
        import pizone.discovery as disco

        if ipOrUId is None:
            disco_list = disco.get_all()
            if len(disco_list) > 1:
                raise ConnectionAbortedError('Multiple iZone instances discovered on network')
            if not len:
                raise ConnectionRefusedError('No iZone device found')

            disco = disco_list[0]
        else:
            try:
                socket.inet_aton(ipOrUId)
                self._ip = ipOrUId  # id is an IP
            except socket.error:
                self._ip = None

            if self._ip is None:
                # id is a common name, try discovery
                disco = disco.get_by_uid(ipOrUId)
                if disco is None:
                    raise ConnectionRefusedError("no device found for %s" % ipOrUId)

        if disco is not None:
            self._ip = disco.addr
            self._device_uid = disco.uid

        self.info = {}
        self.zones = []
        self.fan_modes = []

        self._device_uid = None
        self._system_settings = None

        self.refresh_all(force=True)

    def refresh_all(self, force: bool = False) -> None:
        """Refresh all controller information"""
        self.refresh_system(force)
        self.refresh_zones(force)
        self.refresh_schedules(force)

    @CoolDown(10000)
    def refresh_system(self, force: bool = False) -> None:
        """Refresh the system settings.

        This has a cool-down of 10 seconds, and will only refresh if at least this time
        period has elapsed.

        Args:
            force: If true, force an update ignoring the cool-down.
        """
        self._refresh_system(True)

    def _refresh_system(self, retry):
        values = self._system_settings = self._get_resource('SystemSettings')

        uid = values['AirStreamDeviceUId']
        if self._device_uid is None:
            self._device_uid = uid
        elif self._device_uid != uid:
            if not retry:
                raise ConnectionAbortedError("iZone device has changed it's UID")
            # if the UID we get back is different, try reconnecting and retrying
            self._refresh_ip()
            return self._refresh_system(retry=False)

        self.fan_modes = Controller._VALID_FAN_MODES[values['FanAuto']]

    @CoolDown(10000)
    def refresh_zones(self, force: bool = False) -> None:
        """Refresh the Zone information.

        This has a cool-down of 10 seconds, and will only refresh if at least this time period
        has elapsed.

        Args:
            force: If true, force an update ignoring the cool-down.
        """

        from pizone.zone import Zone
        zones = self.zones_total

        zone_data_part = self._get_resource('Zones1_4')
        if zones > 4:
            zone_data_part.extend(self._get_resource('Zones5_8'))
            if zones > 8:
                zone_data_part.extend(self._get_resource('Zones9_12'))

        for i in range(0, zones):
            zone_data = zone_data_part[i]
            if len(self.zones) <= i:
                self.zones.append(Zone(self, zone_data))
            else:
                self.zones[i]._update_zone(zone_data) #pylint: disable=protected-access

    @CoolDown(10000)
    def refresh_schedules(self, force: bool = False) -> None:
        """TODO: Not implemented"""
        pass

    @property
    def device_ip(self) -> str:
        """IP Address of the unit"""
        return self._ip

    @property
    def device_uid(self) -> str:
        '''UId of the unit'''
        return self._device_uid

    @property
    def state(self) -> bool:
        """True if the system is turned on"""
        return self._get_system_state('SysOn') == 'on'

    @state.setter
    def state(self, value: bool) -> None:
        self._set_system_state('SysOn', 'SystemON', 'on' if value else 'off')

    @property
    def mode(self) -> ControllerMode:
        """System mode, cooling, heating, etc"""
        return ControllerMode(self._get_system_state('SysMode'))

    @mode.setter
    def mode(self, value: Mode):
        self._set_system_state('SysMode', 'SystemMODE', value.value)

    @property
    def fan(self) -> ControllerFan:
        """The fan level. Not all fan modes are allowed.
        Raises:
            AttributeError: On setting if the argument value is not valid
        """
        return ControllerFan(self._get_system_state('SysFan'))

    @fan.setter
    def fan(self, value: Fan) -> None:
        if value not in self.fan_modes:
            raise AttributeError("Fan mode {} not allowed".format(value.value))
        self._set_system_state('SysFan', 'SystemFAN', value.value,
                               'medium' if value is Controller.Fan.MED else value.value)

    @property
    def sleep_timer(self) -> int:
        """The sleep timer.
        Valid settings are 0, 30, 60, 90, 120
        Raises:
            AttributeError: On setting if the argument value is not valid
        """
        return int(self._get_system_state('SleepTimer'))

    @sleep_timer.setter
    def sleep_timer(self, value: int):
        time = int(value)
        if time < 0 or time > 120 or time % 30 != 0:
            raise AttributeError(
                'Invalid Sleep Timer \"{}\", must be divisible by 30'.format(value))
        self._set_system_state('SleepTimer', 'SleepTimer', value, time)

    @property
    def free_air_enabled(self) -> bool:
        """Test if the system has free air system available"""
        return self._get_system_state('FreeAir') == 'disabled'

    @property
    def free_air(self) -> bool:
        """True if the free air system is turned on. False if unavailable or off
        Raises:
            AttributeError: If attempting to set the state of the free air system
                when it is not available.
        """
        return self._get_system_state('FreeAir') == 'on'

    @free_air.setter
    def free_air(self, value: bool) -> None:
        if not self.free_air_enabled:
            raise AttributeError('Free air is disabled')
        self._set_system_state('FreeAir', 'FreeAir', 'on' if value else 'off')

    @property
    def temp_supply(self) -> float:
        """Current supply, or in duct, air temperature."""
        return float(self._get_system_state('Supply'))

    @property
    def temp_setpoint(self) -> float:
        """AC unit setpoint temperature.
        This is the unit target temp with with rasMode == RAS,
        or with rasMode == master and ctrlZone == 13.
        Args:
            value: Valid settings are between ecoMin and ecoMax, at 0.5 degree units.
        Raises:
            AttributeError: On setting if the argument value is not valid.
                Can still be set even if the mode isn't appropriate.
        """
        return float(self._get_system_state('Setpoint'))

    @temp_setpoint.setter
    def temp_setpoint(self, value: float) -> None:
        if value % 0.5 != 0:
            raise AttributeError('SetPoint \'{}\' not rounded to nearest 0.5'.format(value))
        if value < self.temp_min or value > self.temp_max:
            raise AttributeError('SetPoint \'{}\' is out of range'.format(value))
        self._set_system_state('Setpoint', 'UnitSetpoint', value, str(value))

    @property
    def temp_return(self) -> float:
        """The return, or room, air temperature"""
        return float(self._get_system_state('Temp'))

    @property
    def eco_lock(self) -> bool:
        """True if eco lock setting is on."""
        return self._get_system_state('EcoLock') == 'true'

    @property
    def temp_min(self) -> float:
        """The value for the eco lock minimum, or 15 if eco lock not set"""
        return float(self._get_system_state('EcoMin')) if self.eco_lock else 15.0

    @property
    def temp_max(self) -> float:
        """The value for the eco lock maxium, or 30 if eco lock not set"""
        return float(self._get_system_state('EcoMax')) if self.eco_lock else 30.0

    @property
    def ras_mode(self) -> str:
        """This indicates the current selection of the Return Air temperature Sensor.
        Possible values are:
            master: the AC unit is controlled from a CTS, which is manually selected
            RAS:    the AC unit is controller from its own return air sensor
            zones:  the AC unit is controlled from a CTS, which is automatically
                selected dependant on the cooling/ heating need of zones.
        """
        return self._get_system_state('RAS')

    @property
    def zone_ctrl(self) -> int:
        """This indicates the zone that currently controls the AC unit.
        Value interpreted in combination with rasMode"""
        return int(self._get_system_state('CtrlZone'))

    @property
    def zones_total(self) -> int:
        """This indicates the number of zones the system is configured for."""
        return int(self._get_system_state('NoOfZones'))

    @property
    def zones_const(self) -> int:
        """This indicates the number of constant zones the system is configured for."""
        return self._get_system_state('NoOfConst')

    @property
    def sys_type(self) -> str:
        """This indicates the type of the iZone system connected. Possible values are:
        110: the system is zone control only and all the zones are OPEN/CLOSE zones
        210: the system is zone control only. Zones can be temperature controlled,
            dependant on the zone settings.
        310: the system is zone control and unit control.
        """
        return self._get_system_state('SysType')


    def _get_system_state(self, state):
        self.refresh_system()
        return self._system_settings[state]

    def _set_system_state(self, state, command, value, send=None):
        if send is None:
            send = value
        if self._system_settings[state] == value:
            return
        self._send_command(command, send)
        self._system_settings[state] = value

    def _refresh_ip(self):
        from pizone.discovery import get_by_uid
        if self._device_uid is None:
            raise ConnectionRefusedError("Attempted to reconnect IP but no UID set")
        disco = get_by_uid(self.device_uid)
        if disco is None:
            raise ConnectionResetError("Lost connection to the iZone device")
        self._ip = disco['ip']

    def _get_resource(self, resource, retry=True):
        response_json = None
        import requests
        with requests.session() as session:
            try:
                req = session.get('http://%s/%s' % (self.device_ip, resource),
                                  timeout=Controller.REQUEST_TIMEOUT)
                req.raise_for_status()
                response_json = req.json()
            except requests.exceptions.RequestException:
                # Attempt to reconnect to a different IP using netdisco
                if not retry:
                    raise ConnectionResetError("Lost connection to the iZone device")
                self._refresh_ip()
                return self._get_resource(resource, retry=False)

        return response_json

    def _send_command(self, command, data, retry=True):
        import requests
        with requests.session() as session:
            try:
                data = {command : data}
                req = session.post('http://%s/%s' % (self.device_ip, command),
                                   timeout=3, data=json.dumps(data))
                req.raise_for_status()
            except requests.exceptions.RequestException as exc:
                # Attempt to reconnect to a different IP using netdisco
                if not retry:
                    raise ConnectionResetError("Lost connection to the iZone device") from exc
                self._refresh_ip()
                return self._send_command(command, data, retry=False)
