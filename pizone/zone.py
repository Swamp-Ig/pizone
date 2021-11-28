"""
Zone object.

Various properties allow interogation and setting of zone data.
"""

from enum import Enum
from typing import Dict, Union, Optional


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

    def __init__(self, controller, index: int) -> None:
        self._zone_data = {}  # type: Dict
        self._index = index
        self._controller = controller

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
    def temp_setpoint(self) -> Optional[float]:
        """Temp setpoint in degrees C."""
        return self._get_zone_state('SetPoint') or None

    @property
    def temp_current(self) -> Optional[float]:
        """Current zone temperature"""
        return self._get_zone_state('Temp') or None

    @property
    def airflow_max(self) -> int:
        """Max allowed airflow for the zone as a percent"""
        return self._get_zone_state('MaxAir')

    @property
    def airflow_min(self) -> int:
        """Min allowed airflow for the zone as a percent"""
        return self._get_zone_state('MinAir')

    async def set_airflow_min(self, value: int) -> None:
        """
        Change the zone airflow min in 5% increments
        Valid values are percent in 5% increments.
        Raises:
            AttributeError if the set point is out of range
        """
        if value % 5 != 0:
            raise AttributeError(
                'MinAir \'{}\' not rounded to nearest 5'
                .format(value))
        if (value < 0
                or value > 100):
            raise AttributeError(
                'MinAir \'{}\' is out of range'.format(value))

        await self._send_command('AirMinCommand', value)
        self._zone_data['MinAir'] = value
        self._fire_listeners()

    async def set_airflow_max(self, value: int) -> None:
        """
        Change the zone airflow max in 5% increments
        Valid values are percent in 5% increments.
        Raises:
            AttributeError if the set point is out of range
        """
        if value % 5 != 0:
            raise AttributeError(
                'MaxAir \'{}\' not rounded to nearest 5'
                .format(value))
        if (value < 0
                or value > 100):
            raise AttributeError(
                'MaxAir \'{}\' is out of range'.format(value))

        await self._send_command('AirMaxCommand', value)
        self._zone_data['MaxAir'] = value
        self._fire_listeners()

    async def set_temp_setpoint(self, value: float) -> None:
        """
        Change the setpoint for the zone in degrees C.
        Valid values are between the min and max temp on the controller,
        and in half-degree increments
        Raises:
            AttributeError if the set point is out of range
        """
        if self.type != Zone.Type.AUTO:
            raise AttributeError(
                'Can\'t set SetPoint to \'{}\' type zone.'
                .format(self.type))
        if value % 0.5 != 0:
            raise AttributeError(
                'SetPoint \'{}\' not rounded to nearest 0.5'
                .format(value))
        if (value < self._controller.temp_min
                or value > self._controller.temp_max):
            raise AttributeError(
                'SetPoint \'{}\' is out of range'.format(value))

        await self._send_command('ZoneCommand', value)
        self._zone_data['Mode'] = 'auto'
        self._zone_data['SetPoint'] = value
        self._fire_listeners()

    async def set_mode(self, value: Mode) -> None:
        """Set the current zone mode.
        Possible values are:
        'open' – the zone is currently open
        'close' – the zone is currently closed
        'auto' – the zone is currently in temperature control mode
        """
        if value == Zone.Mode.AUTO:
            if self.type != Zone.Type.AUTO:
                raise AttributeError(
                    'Can\'t use auto mode on open/close zone.')
            await self._send_command(
                'ZoneCommand',
                self._get_zone_state('SetPoint'))
            self._zone_data['Mode'] = 'auto'
        else:
            await self._send_command('ZoneCommand', value.value)
            self._zone_data['Mode'] = value.value
        self._fire_listeners()

    def _update_zone(self, zone_data, notify: bool = True):
        if zone_data['Index'] != self._index:
            raise AttributeError('Can\'t change index of existing zone.')
        self._zone_data = zone_data
        if notify:
            self._fire_listeners()

    def _fire_listeners(self) -> None:
        self._controller._discovery.zone_update(self._controller, self)  # pylint: disable=protected-access  # noqa

    def _get_zone_state(self, state):
        self._controller._ensure_connected()  # pylint: disable=protected-access  # noqa
        return self._zone_data[state]

    async def _send_command(self, command, data: Union[str, float, int]):
        send_data = {'ZoneNo': str(self._index+1), 'Command': str(data)}
        await self._controller._send_command_async(command, send_data)  # pylint: disable=protected-access  # noqa: E501
