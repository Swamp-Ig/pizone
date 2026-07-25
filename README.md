# python-izone

Python library for the [iZone](https://izone.com.au/) air conditioning system.

Used by the [Home Assistant iZone integration](https://www.home-assistant.io/integrations/izone/).

## Install

```bash
pip install python-izone
```

## Overview

The main entry points are `Controller` and `Zone` for device control, and
`DiscoveryService` for UDP discovery on the local network.

Synchronous property reads return cached device data and do not raise
`ConnectionError`. Async command and refresh methods perform HTTP I/O and
raise `ConnectionError` when the device cannot be reached. They raise
`ControllerCommandError` when the device responds but rejects the request
(`{ERROR}` body or HTTP 4xx).

## Dependencies

Requires `aiohttp>=3.14.1`. Home Assistant pins `aiohttp==3.14.1`; that version
includes the HTTP POST coalescing fix ([aiohttp#10991](https://github.com/aio-libs/aiohttp/pull/10991))
needed for iZone controllers that read the request in a single operation.

## Protocol documentation

The iZone Ethernet interface is documented in
[AC-DOC-1401-11_iZoneEthernetInterface.pdf](./AC-DOC-1401-11_iZoneEthernetInterface.pdf).

## Troubleshooting / issue reports

If Home Assistant can load the iZone integration, prefer **Download diagnostics**
from the integration page.

When that is not possible (or you need a live HTTP/UDP survey of the bridge),
run the stdlib probe from a machine on the same LAN:

```bash
curl -fsSL -o izone_v1_probe.py \
  https://raw.githubusercontent.com/Swamp-Ig/pizone/main/scripts/izone_v1_probe.py
python3 izone_v1_probe.py <bridge-ip> --with-content-type > izone-v1-probe.log
```

Attach `izone-v1-probe.log` to the GitHub issue. The CLI host (IP or hostname)
and IPv4/IPv6 literals in the log are redacted to `**REDACTED**` by default
(including UDP `IP_…` payloads); use `--show-ips` only for local debugging.
Stop Home Assistant (or other listeners on UDP 7005) first if the UDP listen
step fails to bind.

## Development

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync --all-extras --group dev`
3. `./scripts/check` — lint, type-check, test, and build (same as CI)
4. `./scripts/coverage` — test coverage report (advisory; not part of the CI gate)

Individual commands:

- `uv run pytest tests/`
- `uv run ruff check pizone tests`
- `uv run ruff format pizone tests`
- `uv run mypy pizone`
- `uv build`

To run integration tests against a controller on your network:

```bash
uv run pytest tests/test_fullstack.py -m hardware -o addopts=
```
