"""Zone object. Various properties allow interogation and setting of zone data."""

import json
from enum import Enum
from typing import Dict, TypeVar

import requests

import pizone.controller as ctrl

class ZoneType(Enum):
    """Zone Type enumeration
    This indicates the type of the zone. Possible values are:
    'auto' – the zone has temperature control enabled
    'opcl' – the zone is open/close only
    'const' – the zone is a constant zone
    """
    AUTO = 'auto'
    OPCL = 'opcl'
    CONST = 'const'

class ZoneMode(Enum):
    """This indicates the current mode the zone is in. Possible values are:
    'open' – the zone is currently open
    'close' – the zone is currently closed
    'auto' – the zone is currently in temperature control mode
    """
    OPEN = 'open'
    CLOSE = 'close'
    AUTO = 'auto'

class Zone:
    """Interface to IZone zone"""

    Type = ZoneType
    Mode = ZoneMode
    DictValue = TypeVar('DictValue', str, int, float)
    ZoneData = Dict[str, DictValue]

    def __init__(self, controller: ctrl.Controller, zone_data: ZoneData):
        self._zone_data = zone_data
        self._index = int(zone_data['Index'])
        self.controller = controller

    @property
    def index(self) -> int:
        """The index of the zone"""
        return self._index

    @property
    def name(self) -> str:
        """Zone name"""
        return self._get_zone_state('Name')

    @property
    def type(self) -> ZoneType:
        """This indicates the type of the zone. Possible values are:
        'auto' – the zone has temperature control enabled
        'opcl' – the zone is open/close only
        'const' – the zone is a constant zone
        """
        return ZoneType(self._get_zone_state('Type'))

    @property
    def mode(self) -> ZoneMode:
        """This indicates the current mode the zone is in. Possible values are:
        'open' – the zone is currently open
        'close' – the zone is currently closed
        'auto' – the zone is currently in temperature control mode
        """
        return ZoneMode(self._get_zone_state('Mode'))

    @property
    def temp_setpoint(self) -> float:
        """Temp setpoint in degrees C.
        Valid values are between the min and max temp on the controller,
        and in half-degree increments
        Raises:
            AttributeError if the set point is out of range
        """
        return self._get_zone_state('SetPoint')

    @property
    def temp_current(self) -> float:
        """Current zone temperature"""
        return self._get_zone_state('Temp')

    @property
    def airflow_max(self) -> int:
        """Max allowed airflow for the zone as a percent"""
        return self._get_zone_state('MaxAir')

    @property
    def airflow_min(self) -> int:
        """Min allowed airflow for the zone as a percent"""
        return self._get_zone_state('MinAir')

    @temp_setpoint.setter
    def temp_setpoint(self, value: float) -> None:
        if self.type != ZoneType.AUTO:
            raise AttributeError('Can\'t set SetPoint to \'{}\' type zone.'.format(self.type))
        if value % 0.5 != 0:
            raise AttributeError('SetPoint \'{}\' not rounded to nearest 0.5'.format(value))
        if value < self.controller.temp_min or value > self.controller.temp_max:
            raise AttributeError('SetPoint \'{}\' is out of range'.format(value))
        if self._zone_data['SetPoint'] == value:
            return
        self._send_command('ZoneCommand', value)
        self._zone_data['SetPoint'] = value
        self._zone_data['Mode'] = ZoneMode.AUTO.value

    @mode.setter
    def mode(self, value: ZoneMode) -> None:
        if self.type == ZoneType.CONST:
            raise AttributeError('Can\'t set mode on constant zone.')
        if value == ZoneMode.AUTO:
            if self.type != ZoneType.AUTO:
                raise AttributeError('Can\'t use auto mode on open/close zone.')
            self._send_command('ZoneCommand', self.temp_setpoint)
        else:
            self._send_command('ZoneCommand', value)
        self._zone_data['Mode'] = value

    def _update_zone(self, zone_data):
        if zone_data['Index'] != self._index:
            raise AttributeError('Can\'t change index of existing zone.')
        self._zone_data = zone_data

    def _get_zone_state(self, state):
        self.controller.refresh_zones()
        return self._zone_data[state]

    def _send_command(self, command, data, retry=True):
        with requests.session() as session:
            try:
                payload = {command : {'ZoneNo': str(self._index), 'Command' : str(data)}}
                req = session.post('http://%s/%s' % (self.controller.device_ip, command),
                                   timeout=3, data=json.dumps(payload))
                req.raise_for_status()
            except requests.exceptions.RequestException as exc:
                # Attempt to reconnect to a different IP using netdisco
                if not retry:
                    raise ConnectionResetError("Lost connection to the iZone device") from exc
                self.controller._refresh_ip() #pylint: disable=protected-access
                return self._send_command(command, data, retry=False)
