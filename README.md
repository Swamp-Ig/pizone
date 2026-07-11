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
raise `ConnectionError` when the device cannot be reached.

## Protocol documentation

The iZone Ethernet interface is documented in
[AC-DOC-1401-11_iZoneEthernetInterface.pdf](./AC-DOC-1401-11_iZoneEthernetInterface.pdf).

## Development

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync --all-extras --group dev`
3. `./scripts/check` — lint, type-check, test, and build (same as CI)

Individual commands:

- `uv run pytest tests/`
- `uv run pylint pizone`
- `uv run mypy pizone`
- `uv build`

To run integration tests against a controller on your network:

`uv run pytest tests/test_fullstack.py`
