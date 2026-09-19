"""iZone API v2 wire enums, translation, requests, and V1-shaped normalizers.

V2 reads use ``iZoneRequestV2``; writes use ``iZoneCommandV2`` when the
controller ``_read_api`` is ``\"v2\"``. Content-Type polarity differs by
firmware — probe once and cache; do not thrash.
"""

import asyncio
from enum import IntEnum
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from .controller import Controller
    from .zone import Zone

_LOG = logging.getLogger("pizone.v2")

V2_ERROR_BACKOFF = 0.15


def decode_device_body(raw: bytes) -> str:
    """Decode an iZone HTTP body.

    Prefer UTF-8 (zone names / tags). Some bridges embed raw ``0xFF`` filler in
    fields such as ``LockCode``; fall back to latin-1 only when UTF-8 fails.
    Request bodies are always UTF-8 encoded.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# RAS / FanAuto have no matching library Enum — keep small maps for V1-shaped cache.
_RAS = {
    0: "const",
    1: "master",
    2: "RAS",
    3: "zones",
}
_FAN_AUTO_TYPE = {
    1: "3-speed",
    2: "2-speed",
    3: "4-speed",
    4: "var-speed",
}


class SysOnWire(IntEnum):
    """ASH SysOn_e."""

    OFF = 0
    ON = 1


class SysModeWire(IntEnum):
    """ASH SysMode_e (library Mode names; FREE_AIR is not a SysMode)."""

    COOL = 1
    HEAT = 2
    VENT = 3
    DRY = 4
    AUTO = 5


class SysFanWire(IntEnum):
    """ASH SysFan_e (member names match Controller.Fan)."""

    LOW = 1
    MED = 2
    HIGH = 3
    AUTO = 4
    TOP = 5
    NON_GAS_HEAT = 99


class ZoneTypeWire(IntEnum):
    """ASH ZoneType_e (member names match Zone.Type)."""

    OPCL = 1
    CONST = 2
    AUTO = 3


class ZoneModeWire(IntEnum):
    """ASH ZoneMode_e (member names match Zone.Mode)."""

    OPEN = 1
    CLOSE = 2
    AUTO = 3


def temp_to_wire(celsius: float) -> int:
    """Degrees C → V2 ×100 int."""
    return int(round(float(celsius) * 100))


def temp_from_wire(value: Any) -> float:
    """V2 ×100 (or pass-through float) → degrees C."""
    if value is None:
        return 0.0
    if isinstance(value, float) and value < 100:
        return value
    return float(value) / 100.0


def temp_to_v1_str(value: Any) -> str:
    """V2 ×100 (or pass-through) → V1-style temperature string."""
    if value is None:
        return "0.0"
    if isinstance(value, str):
        return value
    return f"{float(value) / 100.0:.1f}"


def mode_from_wire(raw: int) -> Controller.Mode:
    from .controller import Controller

    return Controller.Mode[SysModeWire(raw).name]


def mode_to_wire(mode: Controller.Mode) -> SysModeWire:
    try:
        return SysModeWire[mode.name]
    except KeyError as ex:
        raise ValueError(f"No SysMode wire for Mode.{mode.name}") from ex


def fan_from_wire(raw: int) -> Controller.Fan:
    from .controller import Controller

    name = SysFanWire(raw).name
    try:
        return Controller.Fan[name]
    except KeyError as ex:
        raise ValueError(f"No library Fan for SysFan wire {raw}") from ex


def fan_to_wire(fan: Controller.Fan) -> SysFanWire:
    try:
        return SysFanWire[fan.name]
    except KeyError as ex:
        raise ValueError(f"No SysFan wire for Fan.{fan.name}") from ex


def zone_mode_from_wire(raw: int) -> Zone.Mode:
    from .zone import Zone

    return Zone.Mode[ZoneModeWire(raw).name]


def zone_mode_to_wire(mode: Zone.Mode) -> ZoneModeWire:
    return ZoneModeWire[mode.name]


def zone_type_from_wire(raw: int) -> Zone.Type:
    from .zone import Zone

    return Zone.Type[ZoneTypeWire(raw).name]


def zone_type_to_wire(ztype: Zone.Type) -> ZoneTypeWire:
    return ZoneTypeWire[ztype.name]


def system_v2_useful(data: dict[str, Any]) -> bool:
    """Return whether a Type=1 response carries usable SystemV2."""
    uid = data.get("AirStreamDeviceUId")
    system = data.get("SystemV2")
    if not isinstance(uid, str) or not uid:
        return False
    if not isinstance(system, dict):
        return False
    return "SysOn" in system and "NoOfZones" in system


def zones_v2_useful(data: dict[str, Any], *, index: int | None = None) -> bool:
    """Return whether a Type=2 response carries usable ZonesV2."""
    zone = data.get("ZonesV2")
    if not isinstance(zone, dict):
        return False
    if "Index" not in zone:
        return False
    if index is not None and int(zone["Index"]) != index:
        return False
    return True


def system_v2_to_settings(
    system_v2: dict[str, Any], uid: str
) -> dict[str, str | int | float]:
    """Normalize SystemV2 into a V1-shaped SystemSettings dict."""
    sys_on = system_v2.get("SysOn", 0)
    if isinstance(sys_on, str):
        on_val = sys_on
    else:
        on_val = "on" if int(sys_on) else "off"

    mode_raw = system_v2.get("SysMode", 1)
    if isinstance(mode_raw, str):
        mode_val = mode_raw
    else:
        try:
            mode_val = mode_from_wire(int(mode_raw)).value
        except ValueError:
            mode_val = "cool"

    fan_raw = system_v2.get("SysFan", SysFanWire.AUTO)
    if isinstance(fan_raw, str):
        fan_val = fan_raw
    else:
        try:
            fan_val = fan_from_wire(int(fan_raw)).value
        except ValueError:
            fan_val = "auto"

    ras_raw = system_v2.get("RAS", 3)
    if isinstance(ras_raw, str):
        ras_val = ras_raw
    else:
        ras_val = _RAS.get(int(ras_raw), "zones")

    eco_lock = system_v2.get("EcoLock", 0)
    if isinstance(eco_lock, str):
        eco_lock_val = eco_lock
    else:
        eco_lock_val = "true" if int(eco_lock) else "false"

    fan_auto_en = int(system_v2.get("FanAutoEn") or 0)
    if not fan_auto_en:
        fan_auto = "disabled"
    else:
        fan_auto = _FAN_AUTO_TYPE.get(
            int(system_v2.get("FanAutoType") or 0), "unknown"
        )

    sys_type = system_v2.get("SysType", "0")
    if not isinstance(sys_type, str):
        sys_type = str(sys_type)

    free_air = system_v2.get("FreeAir", "off")
    if isinstance(free_air, int):
        free_air = "on" if free_air else "off"

    return {
        "AirStreamDeviceUId": uid,
        "SysOn": on_val,
        "SysMode": mode_val,
        "SysFan": fan_val,
        "SleepTimer": int(system_v2.get("SleepTimer") or 0),
        "Supply": temp_to_v1_str(system_v2.get("Supply")),
        "Setpoint": temp_to_v1_str(system_v2.get("Setpoint")),
        "Temp": temp_to_v1_str(system_v2.get("Temp")),
        "RAS": ras_val,
        "CtrlZone": int(system_v2.get("CtrlZone") or 0),
        "Tag1": str(system_v2.get("Tag1") or ""),
        "Tag2": str(system_v2.get("Tag2") or ""),
        "Warnings": str(system_v2.get("Warnings") or "none"),
        "ACError": str(system_v2.get("ACError") or " OK"),
        "EcoLock": eco_lock_val,
        "EcoMax": temp_to_v1_str(system_v2.get("EcoMax", 3000)),
        "EcoMin": temp_to_v1_str(system_v2.get("EcoMin", 1500)),
        "NoOfConst": int(system_v2.get("NoOfConst") or 0),
        "NoOfZones": int(system_v2.get("NoOfZones") or 0),
        "SysType": sys_type,
        "FreeAir": free_air,
        "FanAuto": fan_auto,
    }


def zones_v2_to_zone_data(zone_v2: dict[str, Any]) -> dict[str, str | int | float]:
    """Normalize ZonesV2 into a V1-shaped zone dict (plus V2-only keys)."""
    ztype_raw = zone_v2.get("ZoneType", ZoneTypeWire.AUTO)
    if isinstance(ztype_raw, str):
        ztype = ztype_raw
    else:
        try:
            ztype = zone_type_from_wire(int(ztype_raw)).value
        except ValueError:
            ztype = "auto"

    mode_raw = zone_v2.get("Mode", ZoneModeWire.CLOSE)
    if isinstance(mode_raw, str):
        mode = mode_raw
    else:
        try:
            mode = zone_mode_from_wire(int(mode_raw)).value
        except ValueError:
            mode = "close"

    data: dict[str, str | int | float] = {
        "Index": int(zone_v2["Index"]),
        "Name": str(zone_v2.get("Name") or ""),
        "Type": ztype,
        "Mode": mode,
        "SetPoint": temp_from_wire(zone_v2.get("Setpoint")),
        "Temp": temp_from_wire(zone_v2.get("Temp")),
        "MaxAir": int(zone_v2.get("MaxAir") or 100),
        "MinAir": int(zone_v2.get("MinAir") or 0),
        "Const": int(zone_v2.get("Const") or 0),
        "ConstA": int(zone_v2.get("ConstA") or 0),
        "Master": int(zone_v2.get("Master") or 0),
    }
    for key in (
        "DmpPos",
        "RfSignal",
        "BattVolt",
        "SensorFault",
        "DmpFlt",
        "Bypass",
        "SensType",
        "Area",
        "Rh",
    ):
        if key in zone_v2:
            data[key] = zone_v2[key]
    return data


def system_command_to_v2(
    state: str, value: Any, send: Any
) -> dict[str, Any] | None:
    """Map a V1-style system command to an iZoneCommandV2 body, or None to use V1."""
    del send
    if state == "SysOn":
        on = value in (True, "on", 1, "1")
        return {"SysOn": int(SysOnWire.ON if on else SysOnWire.OFF)}
    if state == "SysMode":
        from .controller import Controller

        mode = (
            value
            if isinstance(value, Controller.Mode)
            else Controller.Mode(str(value))
        )
        return {"SysMode": int(mode_to_wire(mode))}
    if state == "SysFan":
        from .controller import Controller

        fan = (
            value if isinstance(value, Controller.Fan) else Controller.Fan(str(value))
        )
        return {"SysFan": int(fan_to_wire(fan))}
    if state == "SleepTimer":
        return {"SysSleepTimer": int(value)}
    if state == "Setpoint":
        return {"SysSetpoint": temp_to_wire(float(value))}
    # FreeAir and unknown commands stay on V1 endpoints.
    return None


def zone_command_to_v2(
    command: str, data: dict[str, Any], zone_index: int
) -> dict[str, Any]:
    """Map a V1 zone POST body to iZoneCommandV2."""
    inner = data[command]
    cmd_val = str(inner["Command"])
    if command == "AirMinCommand":
        return {
            "ZoneMinAir": {"Index": zone_index, "MinAir": int(float(cmd_val))}
        }
    if command == "AirMaxCommand":
        return {
            "ZoneMaxAir": {"Index": zone_index, "MaxAir": int(float(cmd_val))}
        }
    if command == "ZoneCommand":
        if cmd_val in ("open", "close", "auto"):
            return {
                "ZoneMode": {
                    "Index": zone_index,
                    "Mode": int(ZoneModeWire[cmd_val.upper()]),
                }
            }
        return {
            "ZoneSetpoint": {
                "Index": zone_index,
                "Setpoint": temp_to_wire(float(cmd_val)),
            }
        }
    raise ValueError(f"Unsupported zone command for V2: {command}")


def _parse_v2_body(body: str) -> dict[str, Any] | None:
    text = body.strip()
    if not text or text.startswith("{ERROR"):
        return None
    text = text.removesuffix("{OK}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _post_once(
    session: aiohttp.ClientSession,
    host: str,
    *,
    path: str,
    payload: dict[str, Any],
    use_content_type: bool,
    timeout: float,
) -> str:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if use_content_type else None
    async with session.post(
        f"http://{host}/{path}",
        data=body,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as response:
        if response.status != 200:
            return "{ERROR}"
        return decode_device_body(await response.read())


async def request_v2(
    session: aiohttp.ClientSession,
    host: str,
    *,
    req_type: int,
    no: int = 0,
    use_content_type: bool | None = None,
    timeout: float = 10.0,
) -> tuple[dict[str, Any] | None, bool | None]:
    """POST iZoneRequestV2; return (parsed dict or None, content-type mode used).

    When *use_content_type* is ``None``, try without Content-Type then with
    ``application/json``. Returns the mode that produced a parseable body (even
    if not ``useful``) so callers can cache polarity.
    """
    payload = {"iZoneV2Request": {"Type": req_type, "No": no, "No1": 0}}

    async def once(mode: bool) -> tuple[dict[str, Any] | None, str]:
        body = await _post_once(
            session,
            host,
            path="iZoneRequestV2",
            payload=payload,
            use_content_type=mode,
            timeout=timeout,
        )
        return _parse_v2_body(body), body

    if use_content_type is not None:
        parsed, body = await once(use_content_type)
        if parsed is None and body.strip().startswith("{ERROR"):
            await asyncio.sleep(V2_ERROR_BACKOFF)
            parsed, _body = await once(use_content_type)
        return parsed, use_content_type

    for mode in (False, True):
        parsed, _body = await once(mode)
        if parsed is not None:
            return parsed, mode
    return None, None


async def command_v2(
    session: aiohttp.ClientSession,
    host: str,
    payload: dict[str, Any],
    *,
    use_content_type: bool | None,
    timeout: float = 10.0,
) -> tuple[str, bool | None]:
    """POST iZoneCommandV2; return (raw body text, content-type mode used)."""
    if use_content_type is not None:
        body = await _post_once(
            session,
            host,
            path="iZoneCommandV2",
            payload=payload,
            use_content_type=use_content_type,
            timeout=timeout,
        )
        if body.strip().startswith("{ERROR"):
            await asyncio.sleep(V2_ERROR_BACKOFF)
            body = await _post_once(
                session,
                host,
                path="iZoneCommandV2",
                payload=payload,
                use_content_type=use_content_type,
                timeout=timeout,
            )
        return body, use_content_type

    for mode in (False, True):
        body = await _post_once(
            session,
            host,
            path="iZoneCommandV2",
            payload=payload,
            use_content_type=mode,
            timeout=timeout,
        )
        if not body.strip().startswith("{ERROR"):
            return body, mode
    return "{ERROR}", None


async def fetch_useful_system(
    session: aiohttp.ClientSession,
    host: str,
    *,
    timeout: float = 10.0,
) -> tuple[dict[str, Any], bool] | None:
    """Type=1 with CT adapt; return (raw response, use_content_type) if useful."""
    try:
        parsed, mode = await request_v2(
            session, host, req_type=1, no=0, use_content_type=None, timeout=timeout
        )
    except TimeoutError, aiohttp.ClientError, OSError:
        return None
    if parsed is None or mode is None or not system_v2_useful(parsed):
        return None
    return parsed, mode
