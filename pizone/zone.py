"""Zone object. Various properties allow interogation and setting of zone data."""

from enum import Enum
from typing import Dict, Union, Callable, List

class Zone:
    """Interface to IZone zone"""

    class Type(Enum):
        """Zone Type enumeration
        This indicates the type of the zone. Possible values are:
        'auto' – the zone has temperature control enabled
        'opcl' – the zone is open/close only
        'const' – the zone is a constant zone
        """
        AUTO = 'auto'
        OPCL = 'opcl'
        CONST = 'const'

    class Mode(Enum):
        """This indicates the current mode the zone is in. Possible values are:
        'open' – the zone is currently open
        'close' – the zone is currently closed
        'auto' – the zone is currently in temperature control mode
        """
        OPEN = 'open'
        CLOSE = 'close'
        AUTO = 'auto'

    DictValue = Union[str, int, float]
    ZoneData = Dict[str, DictValue]

    Listener = Callable[['Zone'], None]

    def __init__(self, controller, index: int) -> None:
        self._zone_data: Dict = {}
        self._index = index
        self.controller = controller
        self._listeners: List[Zone.Listener] = []

    def add_listener(self, listener: Listener) -> None:
        """Add a listener for the system updated event"""
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        """Remove listener"""
        self._listeners.remove(listener)

    def _fire_listeners(self) -> None:
        for listener in self._listeners:
            listener(self)


    @property
    def index(self) -> int:
        """The index of the zone"""
        return self._index

    @property
    def name(self) -> str:
        """Zone name"""
        return self._get_zone_state('Name')

    @property
    def type(self) -> 'Type':
        """This indicates the type of the zone. Possible values are:
        'auto' – the zone has temperature control enabled
        'opcl' – the zone is open/close only
        'const' – the zone is a constant zone
        """
        return self.Type(self._get_zone_state('Type'))

    @property
    def mode(self) -> 'Mode':
        """This indicates the current mode the zone is in. Possible values are:
        'open' – the zone is currently open
        'close' – the zone is currently closed
        'auto' – the zone is currently in temperature control mode
        """
        return self.Mode(self._get_zone_state('Mode'))

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

    @temp_setpoint.setter  # type: ignore
    def temp_setpoint(self, value: float) -> None:
        if self.type != Zone.Type.AUTO:
            raise AttributeError('Can\'t set SetPoint to \'{}\' type zone.'.format(self.type))
        if value % 0.5 != 0:
            raise AttributeError('SetPoint \'{}\' not rounded to nearest 0.5'.format(value))
        if value < self.controller.temp_min or value > self.controller.temp_max:
            raise AttributeError('SetPoint \'{}\' is out of range'.format(value))
        if self._zone_data['SetPoint'] == value:
            return

        self._send_zone_command(value)
        self._zone_data['SetPoint'] = value
        self._zone_data['Mode'] = Zone.Mode.AUTO.value
        self._fire_listeners()

    @mode.setter # type: ignore
    def mode(self, value: Mode) -> None:
        if self.type == Zone.Type.CONST:
            raise AttributeError('Can\'t set mode on constant zone.')

        if value == Zone.Mode.AUTO:
            if self.type != Zone.Type.AUTO:
                raise AttributeError('Can\'t use auto mode on open/close zone.')
            self._send_zone_command(self.temp_setpoint)
        else:
            self._send_zone_command(value.value)
        self._zone_data['Mode'] = value.value
        self._fire_listeners()

    def _update_zone(self, zone_data, notify: bool = True):
        if zone_data['Index'] != self._index:
            raise AttributeError('Can\'t change index of existing zone.')
        self._zone_data = zone_data
        if notify:
            self._fire_listeners()

    def _get_zone_state(self, state):
        self.controller._ensure_connected() # pylint: disable=protected-access
        return self._zone_data[state]

    def _send_zone_command(self, data: Union[str, float]):
        send_data = {'ZoneNo': str(self._index), 'Command' : str(data)}
        # pylint: disable=protected-access
        self.controller._send_command('ZoneCommand', send_data)
        # pylint: enable=protected-access
