
import requests
import json

import pizone.controller as ctrl


TYPE_AUTO = 'auto'
TYPE_OPCL = 'opcl'
TYPE_CONST = 'const'

MODE_OPEN = 'open'
MODE_CLOSE = 'close'
MODE_AUTO = 'auto'

class Zone:
    """Interface to IZone zone"""

    def __init__(self, controller:ctrl.Controller, zoneData):
        self._zoneData = zoneData
        self._index = int(zoneData['Index'])
        self.controller = controller

    @property
    def index(self):
        return self._index

    @property
    def name(self):
        return self._get_zone_state('Name')

    @property
    def type(self):
        return self._get_zone_state('Type')

    @property
    def mode(self):
        return self._get_zone_state('Mode')

    @property
    def setPoint(self):
        return self._get_zone_state('SetPoint')

    @property
    def temp(self):
        return self._get_zone_state('Temp')

    @property
    def maxAir(self):
        return self._get_zone_state('MaxAir')

    @property
    def minAir(self):
        return self._get_zone_state('MinAir')

    @setPoint.setter
    def setPoint(self, value):
        if self.type != TYPE_AUTO:
            raise AttributeError('Can\'t set SetPoint to \'{}\' type zone.'.format(self.type))
        point = float(value)
        if point % 0.5 != 0:
            raise AttributeError('SetPoint \'{}\' not rounded to nearest 0.5'.format(point))
        if point < self.controller.ecoMin or point > self.controller.ecoMax:
            raise AttributeError('SetPoint \'{}\' is out of range'.format(point))
        if self._zoneData['SetPoint'] == point:
            return
        self._send_command('ZoneCommand', point)
        self._zoneData['SetPoint'] = point
        self._zoneData['Mode'] = MODE_AUTO

    @mode.setter
    def mode(self, value):
        if value not in [MODE_OPEN, MODE_CLOSE, MODE_AUTO]:
            raise AttributeError('Can\'t set mode to \'{}\'.'.format(value))
        if self.type == TYPE_CONST:
            raise AttributeError('Can\'t set mode on constant zone.')
        if value == MODE_AUTO:
            if self.type != TYPE_AUTO:
                raise AttributeError('Can\'t use auto mode on open/close zone.')
            self._send_command('ZoneCommand', self.setPoint)
        else:
            self._send_command('ZoneCommand', value)
        self._zoneData['Mode'] = value

    def update_zone(self, zoneData):
        if zoneData['Index'] != self._index:
            raise AttributeError('Can\'t change index of existing zone.')
        self._zoneData = zoneData

    def _get_zone_state(self, state):
        self.controller.refresh_zones()
        return self._zoneData[state]

    def _send_command(self, command, data, retry=True):
        with requests.session() as session:
            try:
                payload = { command : { 'ZoneNo': str(self._index), 'Command' : str(data) } }
                r = session.post('http://%s/%s' % (self.controller.ip, command), timeout=3, data=json.dumps(payload))
                r.raise_for_status()
            except requests.exceptions.RequestException as exc:
                # Attempt to reconnect to a different IP using netdisco
                if not retry:
                    raise ConnectionResetError("Lost connection to the iZone device") from exc
                self.controller._refresh_ip()
                return self._send_command(command, data, retry=False)

