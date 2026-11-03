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
import contextlib
from dataclasses import dataclass
import ipaddress
import json
import re
import socket
import sys
import time
from typing import Any
import urllib.error
import urllib.request

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


@dataclass
class _Redaction:
    """Output redaction settings, filled in by ``main``."""

    # When False, ``_print`` strips network ids from every line.
    show_ips: bool = False
    # Exact CLI host (IP or hostname), redacted as a substring wherever it appears.
    host_token: str | None = None


_REDACTION = _Redaction()


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
    host_token = _REDACTION.host_token
    if host_token:
        text = re.sub(re.escape(host_token), REDACTED, text, flags=re.IGNORECASE)
        # urllib IPv6 URL form http://[fe80::1]/...
        text = re.sub(
            re.escape(f"[{host_token}]"),
            f"[{REDACTED}]",
            text,
            flags=re.IGNORECASE,
        )
    text = _IPV4.sub(REDACTED, text)
    return _IPV6_CANDIDATE.sub(_redact_ipv6_match, text)


def _fmt_addr(addr: str) -> str:
    """Format a socket address for logs (always redacted unless ``--show-ips``)."""
    return addr if _REDACTION.show_ips else REDACTED


def _print(*args: object, **kwargs: Any) -> None:
    """Print to stdout, redacting network ids unless ``--show-ips``."""
    if _REDACTION.show_ips:
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
    _print(snippet or "(empty body)")


def _parse_jsonish(body: str) -> Any:
    text = body
    text = text.removesuffix("{OK}")
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


def _send_iasd(sock: socket.socket, addr: tuple[str, int]) -> None:
    try:
        sock.sendto(IASD, addr)
    except OSError as ex:
        _print(f"send IASD → {_fmt_addr(addr[0])}:{addr[1]} failed: {ex}")
        return
    _print(f"sent IASD → {_fmt_addr(addr[0])}:{addr[1]}")


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
            _send_iasd(sock, addr)

    deadline = time.monotonic() + listen_seconds
    count = 0
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except TimeoutError:
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


def _parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def _probe_v1_inventory(
    base: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    """GET V1 SystemSettings and zone groups; return settings, zones, zone count."""
    status, body, dt, err = _req("GET", f"{base}/SystemSettings", content_type=False)
    _show("GET /SystemSettings (V1 inventory)", status, body, dt, err, full=True)
    parsed = _parse_jsonish(body) if status == 200 else None
    settings = parsed if isinstance(parsed, dict) else None
    zone_count = 0
    if settings is not None:
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

    zones: list[dict[str, Any]] = []
    for path in _v1_zone_groups(zone_count or 8):
        status, body, dt, err = _req("GET", f"{base}/{path}", content_type=False)
        _show(f"GET /{path} (V1 inventory)", status, body, dt, err, full=True)
        if status == 200:
            zones.extend(_zones_as_list(_parse_jsonish(body)))

    if zones:
        _print("\n=== V1 zone summary ===")
        for z in zones:
            _print(
                f"  idx={z.get('Index')} name={z.get('Name')!r} "
                f"type={z.get('Type')!r} mode={z.get('Mode')!r} "
                f"setpoint={z.get('SetPoint')!r} temp={z.get('Temp')!r}"
            )
    return settings, zones, zone_count


def _probe_v2_inventory(base: str, zone_count: int, *, with_content_type: bool) -> None:
    """POST V2 SystemV2 and per-zone ZonesV2 requests."""
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
    sys_v2 = parsed.get("SystemV2") if isinstance(parsed, dict) else None
    if isinstance(parsed, dict) and isinstance(sys_v2, dict):
        _print(
            f"\nV2 summary: UID={parsed.get('AirStreamDeviceUId')!r} "
            f"SysOn={sys_v2.get('SysOn')!r} SysMode={sys_v2.get('SysMode')!r} "
            f"SysFan={sys_v2.get('SysFan')!r} NoOfZones={sys_v2.get('NoOfZones')!r}"
        )
        with contextlib.suppress(TypeError, ValueError):
            zone_count = max(zone_count, int(sys_v2.get("NoOfZones") or 0))

    # Prefer V2 zone count when present; fall back to V1 / 8
    for idx in range(zone_count or 8):
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

    if with_content_type:
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
            full=True,
        )


def _probe_system_on(
    base: str, settings: dict[str, Any], ct_modes: list[bool], *, mutate: bool
) -> None:
    """Re-send the current SystemON state, or toggle and restore with --mutate."""
    sys_on = settings.get("SysOn")
    if sys_on not in ("on", "off"):
        _print(f"\nSkipping SystemON write — unexpected SysOn={sys_on!r}")
        return
    target = sys_on
    if mutate:
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
        if mutate and target != sys_on and status == 200:
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


def _probe_zone_command(
    base: str, zones: list[dict[str, Any]], zone: int, ct_modes: list[bool]
) -> None:
    """Re-send the current command for one inventoried V1 zone."""
    z_match = next(
        (z for z in zones if z.get("Index") == zone - 1),
        zones[zone - 1] if len(zones) >= zone else None,
    )
    if z_match is None:
        _print(f"\nSkipping ZoneCommand — no V1 zone data for zone {zone}")
        return
    mode = z_match.get("Mode")
    setpoint = z_match.get("SetPoint")
    if mode == "auto" and setpoint is not None:
        command: Any = setpoint
    elif mode in ("open", "close"):
        command = mode
    else:
        command = mode if mode is not None else setpoint
    payload = {"ZoneCommand": {"ZoneNo": str(zone), "Command": str(command)}}
    for use_ct in ct_modes:
        label = f"POST /ZoneCommand zone={zone} Command={command!r} (no-op)"
        if use_ct:
            label += " + Content-Type"
        status, body, dt, err = _req(
            "POST", f"{base}/ZoneCommand", payload, content_type=use_ct
        )
        _show(label, status, body, dt, err)


def _probe_system_fan(base: str, ct_modes: list[bool]) -> None:
    """POST an out-of-spec V1 SystemFAN value to see how the bridge rejects it."""
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


def main() -> int:
    """Run the probe against the bridge given on the command line."""
    args = _parse_args()
    _REDACTION.show_ips = args.show_ips
    host = args.host.strip().strip("[]")
    _REDACTION.host_token = host or None
    base = f"http://{host}" if ":" not in host else f"http://[{host}]"
    ct_modes = [False, True] if args.with_content_type else [False]

    _print(f"iZone V1/V2 probe against {host}")
    _print(f"python={sys.version.split()[0]}  timeout={TIMEOUT}s")

    if not args.skip_udp:
        _udp_listen(
            host,
            args.udp_seconds,
            send_iasd=True,
            label="UDP discovery ping",
        )

    settings, zones, zone_count = _probe_v1_inventory(base)
    _probe_v2_inventory(base, zone_count, with_content_type=args.with_content_type)

    if args.skip_writes:
        _print("\n(--skip-writes) Done. Please attach the log on the issue. Thanks!")
        return 0

    if settings is None:
        _print("\nSkipping V1 write probes — SystemSettings missing/unparsed.")
        _print("\nDone. Please attach the log on the issue. Thanks!")
        return 0

    _probe_system_on(base, settings, ct_modes, mutate=args.mutate)
    _probe_zone_command(base, zones, args.zone, ct_modes)
    _probe_system_fan(base, ct_modes)

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
