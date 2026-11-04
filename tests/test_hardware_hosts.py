"""Hardware tests for host resolution and verified host handling.

Exercises discover_by_host and create_controller against a live bridge for the
setup-time cases that can't be reproduced on demand: a name, a moved bridge, a
wrong configured address and a stale DNS record. Name resolution is mocked so a
made-up name points at the real bridge; all HTTP and UDP traffic is real.

Needs exclusive use of UDP port 7005, so stop Home Assistant (or anything else
using iZone discovery) on this machine first.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
import socket
from typing import Any
from unittest.mock import patch

import pytest

from pizone import Controller, ControllerEndpoint, DiscoveryService, create_discovery

# Reserved TLD, so it can never resolve for real.
BRIDGE_NAME = "izone-bridge.test"
# TEST-NET-1 is never routed, so a probe to it hangs until the request timeout.
UNROUTED_HOST = "192.0.2.1"
# Nothing iZone answers here, so a probe to it fails fast.
WRONG_HOST = "127.0.0.1"


# Found once per run: each discovery costs a scan wait, and a UDP reply can drop.
_FOUND: list[ControllerEndpoint] = []


@pytest.fixture
async def bridge() -> ControllerEndpoint:
    """Find the first bridge on the LAN, leaving no discovery service running."""
    for _ in range(2):
        if _FOUND:
            break
        service = await create_discovery()
        try:
            _FOUND.extend(await service.discover_all())
        finally:
            await service.close()
    assert _FOUND, "No iZone controller found on the network"
    return _FOUND[0]


@pytest.fixture
async def service(bridge: ControllerEndpoint) -> AsyncIterator[DiscoveryService]:
    """Fresh discovery service with an empty registry, started after *bridge*."""
    del bridge
    discovery = await create_discovery()
    yield discovery
    await discovery.close()


@contextmanager
def _name_resolves_to(*addresses: str) -> Iterator[None]:
    """Make BRIDGE_NAME resolve to *addresses*; other names resolve normally."""
    loop = asyncio.get_running_loop()
    real_getaddrinfo = loop.getaddrinfo

    async def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        if host == BRIDGE_NAME:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
                for address in addresses
            ]
        return await real_getaddrinfo(host, *args, **kwargs)

    with patch.object(loop, "getaddrinfo", side_effect=getaddrinfo):
        yield


@pytest.mark.hardware
async def test_discover_by_host_probes_live_bridge(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    assert await service.discover_by_host(bridge.host) == bridge
    assert discovered == [bridge]


@pytest.mark.hardware
async def test_discover_by_host_resolves_name_to_bridge_ip(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    with _name_resolves_to(bridge.host):
        assert await service.discover_by_host(BRIDGE_NAME) == bridge


@pytest.mark.hardware
async def test_stale_dns_record_does_not_delay_live_address(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    """An unrouted address listed first must not cost a full probe timeout."""
    with _name_resolves_to(UNROUTED_HOST, bridge.host):
        async with asyncio.timeout(Controller.REQUEST_TIMEOUT):
            assert await service.discover_by_host(BRIDGE_NAME) == bridge


@pytest.mark.hardware
async def test_create_controller_by_name_runs_on_ip(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    with _name_resolves_to(bridge.host):
        controller = await service.create_controller(bridge.uid, BRIDGE_NAME)

    assert controller.device_ip == bridge.host
    await controller.refresh_all()
    assert controller.connected


@pytest.mark.hardware
async def test_discover_by_host_repoints_moved_controller(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    """A host search fixes a running controller left at a stale address."""
    seen: list[ControllerEndpoint] = []
    controller = await service.create_controller(
        bridge.uid, bridge.host, on_address_changed=seen.append
    )
    # Simulate the bridge having moved while UDP was blocked.
    controller._ip = WRONG_HOST
    service._endpoints[bridge.uid].endpoint = ControllerEndpoint(
        uid=bridge.uid, host=WRONG_HOST
    )

    assert await service.discover_by_host(bridge.host) == bridge
    await asyncio.sleep(0)

    assert controller.device_ip == bridge.host
    assert seen == [bridge]
    await controller.refresh_all()
    assert controller.connected


@pytest.mark.hardware
async def test_create_controller_wrong_host_falls_back_to_scan(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    seen: list[ControllerEndpoint] = []

    controller = await service.create_controller(
        bridge.uid, WRONG_HOST, on_address_changed=seen.append
    )
    await asyncio.sleep(0)

    assert controller.device_ip == bridge.host
    assert seen == [bridge]


@pytest.mark.hardware
async def test_passive_reply_from_same_ip_is_not_a_move(
    service: DiscoveryService, bridge: ControllerEndpoint
) -> None:
    seen: list[ControllerEndpoint] = []
    controller = await service.create_controller(
        bridge.uid, bridge.host, on_address_changed=seen.append
    )

    await service.scan()
    await asyncio.sleep(3)

    assert any(reply.get("uid") == bridge.uid for reply in service._recent_udp), (
        "No ASPort reply from the bridge; is UDP 7005 free?"
    )
    assert service._endpoints[bridge.uid].verify is None
    assert controller.device_ip == bridge.host
    assert seen == []
