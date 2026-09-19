"""Sample iZoneRequestV2 payloads for unit and discovery tests."""

import json
from typing import Any

from .http_fakes import FakeHttpResponse, FakeHttpSession

# Local dual-stack map (enums / x100 temps) when V2 Type=1 succeeds.
SAMPLE_SYSTEM_V2: dict[str, Any] = {
    "AirStreamDeviceUId": "000025841",
    "SystemV2": {
        "SysOn": 0,
        "SysMode": 2,
        "SysFan": 1,
        "SleepTimer": 0,
        "Supply": 2510,
        "Setpoint": 2250,
        "Temp": 2108,
        "RAS": 3,
        "CtrlZone": 0,
        "Tag1": "iZone",
        "Tag2": "",
        "Warnings": "none",
        "ACError": " OK",
        "EcoLock": 1,
        "EcoMax": 3000,
        "EcoMin": 1500,
        "NoOfConst": 1,
        "NoOfZones": 2,
        "SysType": "320",
        "FreeAir": 0,
        "FanAutoEn": 1,
        "FanAutoType": 1,
    },
}

SAMPLE_ZONES_V2: dict[int, dict[str, Any]] = {
    0: {
        "AirStreamDeviceUId": "000025841",
        "ZonesV2": {
            "Index": 0,
            "Name": "Kitchen",
            "ZoneType": 3,
            "Mode": 3,
            "Setpoint": 2250,
            "Temp": 2108,
            "MaxAir": 60,
            "MinAir": 0,
            "Const": 255,
            "ConstA": 0,
            "Master": 0,
            "DmpPos": 45,
            "RfSignal": 80,
            "BattVolt": 310,
            "SensorFault": 0,
        },
    },
    1: {
        "AirStreamDeviceUId": "000025841",
        "ZonesV2": {
            "Index": 1,
            "Name": "Lounge",
            "ZoneType": 1,
            "Mode": 2,
            "Setpoint": 2300,
            "Temp": 2280,
            "MaxAir": 90,
            "MinAir": 0,
            "Const": 255,
            "ConstA": 0,
            "Master": 0,
            "BattVolt": 295,
        },
    },
}


def _parse_v2_request(data: object) -> tuple[int, int]:
    if isinstance(data, (bytes, bytearray)):
        payload = json.loads(data.decode("utf-8"))
    elif isinstance(data, str):
        payload = json.loads(data)
    else:
        payload = data
    assert isinstance(payload, dict)
    req = payload["iZoneV2Request"]
    return int(req["Type"]), int(req["No"])


def _loads_post_body(data: object) -> dict[str, Any]:
    if isinstance(data, (bytes, bytearray)):
        payload = json.loads(data.decode("utf-8"))
    elif isinstance(data, str):
        payload = json.loads(data)
    else:
        payload = data
    assert isinstance(payload, dict)
    return payload


def v2_routing_session(
    *,
    system: dict[str, Any] | None = SAMPLE_SYSTEM_V2,
    zones: dict[int, dict[str, Any]] | None = SAMPLE_ZONES_V2,
    use_content_type: bool | None = False,
    get_response: FakeHttpResponse | None = None,
    error_body: str = "{ERROR}",
    command_ok: bool = True,
) -> FakeHttpSession:
    """Fake session that serves useful V2 Type=1/2, optionally requiring CT."""

    def post_response(url: object, **kwargs: object) -> FakeHttpResponse:
        headers = kwargs.get("headers")
        has_ct = isinstance(headers, dict) and "Content-Type" in headers
        if use_content_type is True and not has_ct:
            return FakeHttpResponse(200, body=error_body)
        if use_content_type is False and has_ct:
            return FakeHttpResponse(200, body=error_body)

        url_s = str(url)
        data = kwargs.get("data")
        if "iZoneCommandV2" in url_s:
            _loads_post_body(data)  # validate UTF-8 JSON body
            if command_ok:
                return FakeHttpResponse(200, body="{OK}")
            return FakeHttpResponse(200, body=error_body)

        req_type, no = _parse_v2_request(data)
        if req_type == 1:
            if system is None:
                return FakeHttpResponse(200, body=error_body)
            return FakeHttpResponse(200, body=json.dumps(system))
        if req_type == 2:
            zone_map = zones or {}
            zone = zone_map.get(no)
            if zone is None:
                return FakeHttpResponse(200, body=error_body)
            return FakeHttpResponse(200, body=json.dumps(zone))
        return FakeHttpResponse(200, body=error_body)

    return FakeHttpSession(
        get_response=get_response,
        post_response=post_response,
    )
