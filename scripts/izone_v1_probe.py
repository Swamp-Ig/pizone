#!/usr/bin/env python3
"""Probe iZone bridge: V1 vs V2 HTTP + UDP discovery inventory.

Stdlib only. Safe by default: write probes re-send the *current* SystemON /
zone command (no-op). Pass --mutate only if you intentionally want a toggle.

Network identifiers in stdout are redacted to **REDACTED** (same idea as
Home Assistant iZone diagnostics): the CLI host argument (IP or hostname),
IPv4/IPv6 literals (including UDP ``IP_…`` payloads), and socket peer
addresses. Pass ``--show-ips`` only for local debugging — do not attach that
log to a public issue.

Usage:
  python3 izone_v1_probe.py <bridge-ip> --with-content-type > izone-v1-probe.log
  python3 izone_v1_probe.py 10.0.0.90 --zone 1
  python3 izone_v1_probe.py 10.0.0.90 --udp-seconds 5

Canonical copy: scripts/izone_v1_probe.py on the pizone main branch.
Attach the log on the GitHub issue.
Stop Home Assistant / other iZone listeners first if UDP bind on :7005 fails.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any


TIMEOUT = 10.0
DISCOVERY_PORT = 12107
LISTEN_PORT = 7005
IASD = b"IASD"
REDACTED = "**REDACTED**"
_IPV4 = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
# Candidates only; confirmed with ipaddress.IPv6Address (handles ``::``, zone ids).
_IPV6_CANDIDATE = re.compile(
    r"\[?(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\]?"
    r"|\[?[0-9a-fA-F:]*::[0-9a-fA-F:]*(?:%[0-9A-Za-z._-]+)?\]?"
)

# Set from ``main``; when False, ``_print`` strips network ids from every line.
_SHOW_IPS = False
# Exact CLI host (IP or hostname), redacted as a substring wherever it appears.
_HOST_TOKEN: str | None = None


def _redact_ipv6_match(match: re.Match[str]) -> str:
    raw = match.group(0)
    core = raw.strip("[]").split("%", 1)[0]
    try:
        ipaddress.IPv6Address(core)
    except ValueError:
        return raw
    if raw.startswith("[") and raw.endswith("]"):
        return f"[{REDACTED}]"
    return REDACTED


def _redact_network(text: str) -> str:
    """Redact CLI host, IPv4/IPv6 literals (e.g. UDP ``IP_…`` payloads)."""
    if _HOST_TOKEN:
        text = re.sub(re.escape(_HOST_TOKEN), REDACTED, text, flags=re.IGNORECASE)
        # urllib IPv6 URL form http://[fe80::1]/...
        text = re.sub(
            re.escape(f"[{_HOST_TOKEN}]"),
            f"[{REDACTED}]",
            text,
            flags=re.IGNORECASE,
        )
    text = _IPV4.sub(REDACTED, text)
    return _IPV6_CANDIDATE.sub(_redact_ipv6_match, text)


def _fmt_addr(addr: str) -> str:
    """Format a socket address for logs (always redacted unless ``--show-ips``)."""
    return addr if _SHOW_IPS else REDACTED


def _print(*args: object, **kwargs: Any) -> None:
    """Print to stdout, redacting network ids unless ``--show-ips``."""
    if _SHOW_IPS:
        print(*args, **kwargs)
        return
    print(
        *(_redact_network(a) if isinstance(a, str) else a for a in args),
        **kwargs,
    )


def _req(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    content_type: bool,
) -> tuple[int | None, str, float, str | None]:
    if body is None:
        request = urllib.request.Request(url, method=method)
    else:
        data = json.dumps(body).encode("latin_1")
        headers = {"Content-Length": str(len(data))}
        if content_type:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            raw = resp.read()
            text = raw.decode("latin_1", errors="replace")
            return resp.status, text, time.monotonic() - t0, None
    except urllib.error.HTTPError as ex:
        text = ex.read().decode("latin_1", errors="replace")
        return ex.code, text, time.monotonic() - t0, f"HTTPError: {ex.reason}"
    except Exception as ex:  # noqa: BLE001 — probe script; report anything
        return None, "", time.monotonic() - t0, f"{type(ex).__name__}: {ex}"


def _show(
    label: str,
    status: int | None,
    body: str,
    dt: float,
    err: str | None,
    *,
    full: bool = False,
) -> None:
    _print(f"\n=== {label} ===")
    _print(f"status={status}  time={dt:.3f}s" + (f"  error={err}" if err else ""))
    if full:
        parsed = _parse_jsonish(body)
        if parsed is not None:
            _print(json.dumps(parsed, indent=2, ensure_ascii=False))
            return
    snippet = body if len(body) <= 1200 else body[:1200] + f"… ({len(body)} chars)"
    _print(snippet if snippet else "(empty body)")


def _parse_jsonish(body: str) -> Any:
    text = body
    if text.endswith("{OK}"):
        text = text[:-4]
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _zones_as_list(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        return [z for z in parsed if isinstance(z, dict)]
    if isinstance(parsed, dict):
        inner = parsed.get("Zones")
        if isinstance(inner, list):
            return [z for z in inner if isinstance(z, dict)]
    return []


def _v1_zone_groups(zone_count: int) -> list[str]:
    """V1 grouped GET paths covering zone_count zones (incl. Zones13_14)."""
    n = max(zone_count, 1)
    paths = ["Zones1_4"]
    if n > 4:
        paths.append("Zones5_8")
    if n > 8:
        paths.append("Zones9_12")
    if n > 12:
        paths.append("Zones13_14")
    return paths


def _udp_listen(
    host: str,
    listen_seconds: float,
    *,
    send_iasd: bool,
    label: str,
) -> None:
    _print(
        f"\n=== {label} (bind :{LISTEN_PORT}, "
        f"{'IASD → :' + str(DISCOVERY_PORT) + ', ' if send_iasd else ''}"
        f"{listen_seconds:.1f}s) ==="
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError as ex:
        _print(f"SO_BROADCAST failed: {ex}")
    try:
        sock.bind(("0.0.0.0", LISTEN_PORT))
    except OSError as ex:
        _print(
            f"bind :{LISTEN_PORT} failed: {ex}\n"
            "  (Stop HA / izone-v2 / another probe using that port, then retry.)"
        )
        return

    sock.settimeout(0.5)
    if send_iasd:
        for addr in (("255.255.255.255", DISCOVERY_PORT), (host, DISCOVERY_PORT)):
            try:
                sock.sendto(IASD, addr)
                _print(f"sent IASD → {_fmt_addr(addr[0])}:{addr[1]}")
            except OSError as ex:
                _print(f"send IASD → {_fmt_addr(addr[0])}:{addr[1]} failed: {ex}")

    deadline = time.monotonic() + listen_seconds
    count = 0
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError as ex:
            _print(f"recv failed: {ex}")
            break
        text = data.decode("latin_1", errors="replace")
        count += 1
        kind = (
            "ASPort"
            if text.startswith("ASPort_")
            else "iZoneChanged"
            if text.startswith("iZoneChanged")
            else "other"
        )
        _print(f"  [{kind}] from {_fmt_addr(addr[0])}:{addr[1]}  {text!r}")

    _print(f"UDP packets received: {count}")
    sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Bridge IP address")
    parser.add_argument(
        "--zone",
        type=int,
        default=1,
        help="1-based zone number for ZoneCommand no-op (default: 1)",
    )
    parser.add_argument(
        "--with-content-type",
        action="store_true",
        help="Also retry POSTs with Content-Type: application/json",
    )
    parser.add_argument(
        "--mutate",
        action="store_true",
        help="Allow a SystemON off→on or on→off toggle (default: no-op only)",
    )
    parser.add_argument(
        "--udp-seconds",
        type=float,
        default=3.0,
        help="How long to listen after IASD (default: 3)",
    )
    parser.add_argument(
        "--skip-udp",
        action="store_true",
        help="Skip UDP discovery / listen",
    )
    parser.add_argument(
        "--skip-writes",
        action="store_true",
        help="Inventory + UDP only (no SystemON/ZoneCommand/FAN probes)",
    )
    parser.add_argument(
        "--show-ips",
        action="store_true",
        help="Leave host/IP addresses in stdout (local debug only; do not attach)",
    )
    args = parser.parse_args()
    global _SHOW_IPS, _HOST_TOKEN
    _SHOW_IPS = args.show_ips
    host = args.host.strip().strip("[]")
    _HOST_TOKEN = host or None
    base = f"http://{host}" if ":" not in host else f"http://[{host}]"
    ct_modes = [False]
    if args.with_content_type:
        ct_modes.append(True)

    _print(f"iZone V1/V2 probe against {host}")
    _print(f"python={sys.version.split()[0]}  timeout={TIMEOUT}s")

    if not args.skip_udp:
        _udp_listen(
            host,
            args.udp_seconds,
            send_iasd=True,
            label="UDP discovery ping",
        )

    # --- V1 system inventory ---
    status, body, dt, err = _req("GET", f"{base}/SystemSettings", content_type=False)
    _show("GET /SystemSettings (V1 inventory)", status, body, dt, err, full=True)
    settings = _parse_jsonish(body) if status == 200 else None
    zone_count = 0
    if isinstance(settings, dict):
        try:
            zone_count = int(settings.get("NoOfZones") or 0)
        except (TypeError, ValueError):
            zone_count = 0
        _print(
            f"\nsummary: UID={settings.get('AirStreamDeviceUId')!r} "
            f"SysType={settings.get('SysType')!r} "
            f"NoOfZones={zone_count} SysOn={settings.get('SysOn')!r} "
            f"SysMode={settings.get('SysMode')!r} SysFan={settings.get('SysFan')!r}"
        )

    # --- V1 zone inventory ---
    all_v1_zones: list[dict[str, Any]] = []
    for path in _v1_zone_groups(zone_count or 8):
        status, body, dt, err = _req("GET", f"{base}/{path}", content_type=False)
        _show(f"GET /{path} (V1 inventory)", status, body, dt, err, full=True)
        if status == 200:
            all_v1_zones.extend(_zones_as_list(_parse_jsonish(body)))

    if all_v1_zones:
        _print("\n=== V1 zone summary ===")
        for z in all_v1_zones:
            _print(
                f"  idx={z.get('Index')} name={z.get('Name')!r} "
                f"type={z.get('Type')!r} mode={z.get('Mode')!r} "
                f"setpoint={z.get('SetPoint')!r} temp={z.get('Temp')!r}"
            )

    # --- V2 system + zones inventory ---
    status, body, dt, err = _req(
        "POST",
        f"{base}/iZoneRequestV2",
        {"iZoneV2Request": {"Type": 1, "No": 0, "No1": 0}},
        content_type=False,
    )
    _show(
        "POST /iZoneRequestV2 Type=1 SystemV2 (inventory)",
        status,
        body,
        dt,
        err,
        full=True,
    )
    parsed = _parse_jsonish(body) if status == 200 else None
    if isinstance(parsed, dict):
        sys_v2 = parsed.get("SystemV2")
        if isinstance(sys_v2, dict):
            _print(
                f"\nV2 summary: UID={parsed.get('AirStreamDeviceUId')!r} "
                f"SysOn={sys_v2.get('SysOn')!r} SysMode={sys_v2.get('SysMode')!r} "
                f"SysFan={sys_v2.get('SysFan')!r} NoOfZones={sys_v2.get('NoOfZones')!r}"
            )
            try:
                zone_count = max(zone_count, int(sys_v2.get("NoOfZones") or 0))
            except (TypeError, ValueError):
                pass

    # Prefer V2 zone count when present; fall back to V1 / 8
    v2_zones = zone_count or 8
    for idx in range(v2_zones):
        status, body, dt, err = _req(
            "POST",
            f"{base}/iZoneRequestV2",
            {"iZoneV2Request": {"Type": 2, "No": idx, "No1": 0}},
            content_type=False,
        )
        _show(
            f"POST /iZoneRequestV2 Type=2 ZonesV2 No={idx} (inventory)",
            status,
            body,
            dt,
            err,
            full=True,
        )

    if args.skip_writes:
        _print("\n(--skip-writes) Done. Please attach the log on the issue. Thanks!")
        return 0

    # --- Content-Type comparison on V2 request (shape probe) ---
    if args.with_content_type:
        status, body, dt, err = _req(
            "POST",
            f"{base}/iZoneRequestV2",
            {"iZoneV2Request": {"Type": 1, "No": 0, "No1": 0}},
            content_type=True,
        )
        _show(
            "POST /iZoneRequestV2 Type=1 + Content-Type (shape probe)",
            status,
            body,
            dt,
            err,
        )

    # --- safe writes ---
    if not isinstance(settings, dict):
        _print("\nSkipping V1 write probes — SystemSettings missing/unparsed.")
        _print("\nDone. Please attach the log on the issue. Thanks!")
        return 0

    sys_on = settings.get("SysOn")
    if sys_on in ("on", "off"):
        target = sys_on
        if args.mutate:
            target = "off" if sys_on == "on" else "on"
            _print(f"\n--mutate: toggling SystemON {sys_on!r} → {target!r}")
        payload = {"SystemON": target}
        for use_ct in ct_modes:
            label = (
                f"POST /SystemON {{{target!r}}} (no-op)"
                if target == sys_on
                else f"POST /SystemON {{{target!r}}} (mutate)"
            )
            if use_ct:
                label += " + Content-Type"
            status, body, dt, err = _req(
                "POST", f"{base}/SystemON", payload, content_type=use_ct
            )
            _show(label, status, body, dt, err)
            if args.mutate and target != sys_on and status == 200:
                status, body, dt, err = _req(
                    "POST",
                    f"{base}/SystemON",
                    {"SystemON": sys_on},
                    content_type=use_ct,
                )
                _show(
                    f"POST /SystemON restore {{{sys_on!r}}}"
                    + (" + Content-Type" if use_ct else ""),
                    status,
                    body,
                    dt,
                    err,
                )
    else:
        _print(f"\nSkipping SystemON write — unexpected SysOn={sys_on!r}")

    # Zone no-op from inventoried V1 zones
    z_match = next(
        (z for z in all_v1_zones if z.get("Index") == args.zone - 1),
        all_v1_zones[args.zone - 1] if len(all_v1_zones) >= args.zone else None,
    )
    if z_match is not None:
        mode = z_match.get("Mode")
        setpoint = z_match.get("SetPoint")
        if mode == "auto" and setpoint is not None:
            command: Any = setpoint
        elif mode in ("open", "close"):
            command = mode
        else:
            command = mode if mode is not None else setpoint
        payload = {
            "ZoneCommand": {"ZoneNo": str(args.zone), "Command": str(command)}
        }
        for use_ct in ct_modes:
            label = f"POST /ZoneCommand zone={args.zone} Command={command!r} (no-op)"
            if use_ct:
                label += " + Content-Type"
            status, body, dt, err = _req(
                "POST", f"{base}/ZoneCommand", payload, content_type=use_ct
            )
            _show(label, status, body, dt, err)
    else:
        _print(f"\nSkipping ZoneCommand — no V1 zone data for zone {args.zone}")

    for use_ct in ct_modes:
        label = 'POST /SystemFAN {"SystemFAN":"top"} (V1 out-of-spec probe)'
        if use_ct:
            label += " + Content-Type"
        status, body, dt, err = _req(
            "POST",
            f"{base}/SystemFAN",
            {"SystemFAN": "top"},
            content_type=use_ct,
        )
        _show(label, status, body, dt, err)

    # Brief post-write listen for iZoneChanged_* (no second IASD)
    if not args.skip_udp:
        _udp_listen(
            host,
            min(args.udp_seconds, 3.0),
            send_iasd=False,
            label="UDP post-write listen (iZoneChanged_*?)",
        )

    _print("\nDone. Please attach the log on the issue. Thanks!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

