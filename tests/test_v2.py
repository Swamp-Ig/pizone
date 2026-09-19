"""Unit tests for pizone.v2 normalizers and Content-Type adapt."""

import json
from typing import cast

from aiohttp import ClientSession
import pytest

from pizone import Controller, Zone, v2 as v2_mod

from .http_fakes import FakeHttpResponse, FakeHttpSession
from .v2_fixtures import SAMPLE_SYSTEM_V2, SAMPLE_ZONES_V2, v2_routing_session


def test_system_v2_useful_requires_core_keys() -> None:
    assert v2_mod.system_v2_useful(SAMPLE_SYSTEM_V2) is True
    assert v2_mod.system_v2_useful({"AirStreamDeviceUId": "x"}) is False
    assert (
        v2_mod.system_v2_useful({"AirStreamDeviceUId": "x", "SystemV2": {"SysOn": 1}})
        is False
    )


def test_zones_v2_useful_matches_index() -> None:
    assert v2_mod.zones_v2_useful(SAMPLE_ZONES_V2[0], index=0) is True
    assert v2_mod.zones_v2_useful(SAMPLE_ZONES_V2[0], index=1) is False
    assert v2_mod.zones_v2_useful({"ZonesV2": {"Name": "x"}}) is False


def test_decode_device_body_prefers_utf8_falls_back_for_lockcode() -> None:
    assert v2_mod.decode_device_body('{"Tag1":"Café"}'.encode()) == '{"Tag1":"Café"}'
    raw = b'{"LockCode":"\xff\xff"}'
    assert v2_mod.decode_device_body(raw) == '{"LockCode":"ÿÿ"}'


def test_sys_fan_wire_auto_is_4_top_is_5() -> None:
    assert v2_mod.SysFanWire.AUTO == 4
    assert v2_mod.SysFanWire.TOP == 5
    assert v2_mod.fan_from_wire(4) is Controller.Fan.AUTO
    assert v2_mod.fan_from_wire(5) is Controller.Fan.TOP
    assert v2_mod.fan_to_wire(Controller.Fan.AUTO) is v2_mod.SysFanWire.AUTO
    assert v2_mod.fan_to_wire(Controller.Fan.TOP) is v2_mod.SysFanWire.TOP


def test_mode_fan_zone_enum_round_trips() -> None:
    for mode in (
        Controller.Mode.COOL,
        Controller.Mode.HEAT,
        Controller.Mode.VENT,
        Controller.Mode.DRY,
        Controller.Mode.AUTO,
    ):
        assert v2_mod.mode_from_wire(int(v2_mod.mode_to_wire(mode))) is mode
    for fan in Controller.Fan:
        assert v2_mod.fan_from_wire(int(v2_mod.fan_to_wire(fan))) is fan
    for zmode in Zone.Mode:
        assert (
            v2_mod.zone_mode_from_wire(int(v2_mod.zone_mode_to_wire(zmode))) is zmode
        )
    for ztype in Zone.Type:
        assert (
            v2_mod.zone_type_from_wire(int(v2_mod.zone_type_to_wire(ztype))) is ztype
        )


def test_free_air_has_no_sys_mode_wire() -> None:
    with pytest.raises(ValueError):
        v2_mod.mode_to_wire(Controller.Mode.FREE_AIR)


def test_system_command_to_v2_shapes() -> None:
    assert v2_mod.system_command_to_v2("SysOn", "on", "on") == {"SysOn": 1}
    assert v2_mod.system_command_to_v2("SysOn", "off", "off") == {"SysOn": 0}
    assert v2_mod.system_command_to_v2("SysMode", Controller.Mode.HEAT, "heat") == {
        "SysMode": 2
    }
    assert v2_mod.system_command_to_v2("SysFan", Controller.Fan.AUTO, "auto") == {
        "SysFan": 4
    }
    assert v2_mod.system_command_to_v2("SysFan", Controller.Fan.TOP, "top") == {
        "SysFan": 5
    }
    assert v2_mod.system_command_to_v2("Setpoint", 22.5, "22.5") == {
        "SysSetpoint": 2250
    }
    assert v2_mod.system_command_to_v2("SleepTimer", 30, 30) == {"SysSleepTimer": 30}
    assert v2_mod.system_command_to_v2("FreeAir", "on", "on") is None


def test_zone_command_to_v2_shapes() -> None:
    assert v2_mod.zone_command_to_v2(
        "ZoneCommand",
        {"ZoneCommand": {"ZoneNo": "1", "Command": "open"}},
        0,
    ) == {"ZoneMode": {"Index": 0, "Mode": 1}}
    assert v2_mod.zone_command_to_v2(
        "ZoneCommand",
        {"ZoneCommand": {"ZoneNo": "2", "Command": "22.5"}},
        1,
    ) == {"ZoneSetpoint": {"Index": 1, "Setpoint": 2250}}
    assert v2_mod.zone_command_to_v2(
        "AirMinCommand",
        {"AirMinCommand": {"ZoneNo": "1", "Command": "20"}},
        0,
    ) == {"ZoneMinAir": {"Index": 0, "MinAir": 20}}
    assert v2_mod.zone_command_to_v2(
        "AirMaxCommand",
        {"AirMaxCommand": {"ZoneNo": "1", "Command": "80"}},
        0,
    ) == {"ZoneMaxAir": {"Index": 0, "MaxAir": 80}}


def test_system_v2_to_settings_enums_and_temps() -> None:
    settings = v2_mod.system_v2_to_settings(
        cast(dict, SAMPLE_SYSTEM_V2["SystemV2"]), "000025841"
    )
    assert settings["AirStreamDeviceUId"] == "000025841"
    assert settings["SysOn"] == "off"
    assert settings["SysMode"] == "heat"
    assert settings["SysFan"] == "low"
    assert settings["RAS"] == "zones"
    assert settings["Setpoint"] == "22.5"
    assert settings["Temp"] == "21.1"
    assert settings["EcoLock"] == "true"
    assert settings["EcoMax"] == "30.0"
    assert settings["NoOfZones"] == 2
    assert settings["FanAuto"] == "3-speed"
    assert settings["FreeAir"] == "off"


def test_zones_v2_to_zone_data_preserves_battery() -> None:
    zone = v2_mod.zones_v2_to_zone_data(cast(dict, SAMPLE_ZONES_V2[0]["ZonesV2"]))
    assert zone["Index"] == 0
    assert zone["Name"] == "Kitchen"
    assert zone["Type"] == "auto"
    assert zone["Mode"] == "auto"
    assert zone["SetPoint"] == 22.5
    assert abs(float(zone["Temp"]) - 21.08) < 0.001
    assert zone["BattVolt"] == 310
    assert zone["DmpPos"] == 45
    assert zone["RfSignal"] == 80


@pytest.mark.asyncio
async def test_request_v2_adapts_content_type_no_ct_first() -> None:
    """Local dual: useful body without Content-Type; +CT returns {ERROR}."""
    session = v2_routing_session(use_content_type=False)
    parsed, mode = await v2_mod.request_v2(
        cast(ClientSession, session),
        "10.0.0.90",
        req_type=1,
        use_content_type=None,
    )
    assert parsed is not None
    assert mode is False
    assert session.post_calls == 1


@pytest.mark.asyncio
async def test_request_v2_adapts_content_type_with_ct() -> None:
    """Reporter V2-only: no-CT {ERROR}, +CT useful."""
    session = v2_routing_session(use_content_type=True)
    parsed, mode = await v2_mod.request_v2(
        cast(ClientSession, session),
        "10.0.0.90",
        req_type=1,
        use_content_type=None,
    )
    assert parsed is not None
    assert mode is True
    assert session.post_calls == 2


@pytest.mark.asyncio
async def test_request_v2_retries_error_when_mode_cached() -> None:
    calls = {"n": 0}

    def post_response(*_a: object, **_k: object) -> FakeHttpResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeHttpResponse(200, body="{ERROR}")
        return FakeHttpResponse(200, body=json.dumps(SAMPLE_SYSTEM_V2))

    session = FakeHttpSession(post_response=post_response)
    parsed, mode = await v2_mod.request_v2(
        cast(ClientSession, session),
        "10.0.0.90",
        req_type=1,
        use_content_type=False,
    )
    assert mode is False
    assert parsed is not None
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_command_v2_posts_utf8_json_and_ct_mode() -> None:
    session = v2_routing_session(use_content_type=False)
    body, mode = await v2_mod.command_v2(
        cast(ClientSession, session),
        "10.0.0.90",
        {"SysOn": 1},
        use_content_type=False,
    )
    assert body.endswith("{OK}") or body == "{OK}"
    assert mode is False
    data = session.last_post_kwargs["data"]
    assert isinstance(data, (bytes, bytearray))
    assert json.loads(data.decode("utf-8")) == {"SysOn": 1}


@pytest.mark.asyncio
async def test_fetch_useful_system_rejects_empty_shell() -> None:
    shell = {
        "AirStreamDeviceUId": "000025841",
        "SystemV2": {"SysOn": 1},  # missing NoOfZones
    }
    session = FakeHttpSession(
        post_response=FakeHttpResponse(200, body=json.dumps(shell))
    )
    assert (
        await v2_mod.fetch_useful_system(cast(ClientSession, session), "10.0.0.90")
        is None
    )
