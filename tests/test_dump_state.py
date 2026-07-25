"""Tests for dump_state snapshots on discovery, controller, and zone."""

from typing import cast

import pytest

from pizone import ControllerEndpoint
from pizone.discovery import UDP_REPLY_BUFFER_SIZE

from .conftest import MockController, MockDiscoveryService


@pytest.mark.asyncio
async def test_zone_dump_state(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])
    zone = controller.zones[1]

    dumped = zone.dump_state()

    assert dumped["Name"] == "LOUNGE"
    assert dumped["Index"] == 1


@pytest.mark.asyncio
async def test_controller_dump_state(service: MockDiscoveryService) -> None:
    controller = cast(MockController, service._controllers["000000001"])

    dumped = controller.dump_state()

    assert dumped["device_uid"] == "000000001"
    assert dumped["device_ip"] == "192.0.2.1" or isinstance(dumped["device_ip"], str)
    assert dumped["connected"] is True
    assert dumped["bridge_connected"] is True
    assert "system_settings" in dumped
    assert dumped["system_settings"]["AirStreamDeviceUId"] == "000000001"
    assert isinstance(dumped["fan_modes"], list)
    assert len(dumped["zones"]) == len(controller.zones)
    assert dumped["zones"][1]["Index"] == 1
    assert dumped["power"] is None or isinstance(dumped["power"], dict)


@pytest.mark.asyncio
async def test_discovery_dump_state(service: MockDiscoveryService) -> None:
    service._recent_udp.clear()
    service._claimed_endpoints["000000001"] = ControllerEndpoint(
        uid="000000001", host="192.0.2.1"
    )
    service._known_endpoints["000000002"] = ControllerEndpoint(
        uid="000000002", host="192.0.2.2"
    )

    dumped = service.dump_state()

    assert dumped["closed"] is False or isinstance(dumped["closed"], bool)
    assert "udp_bound" in dumped
    assert {"uid": "000000001", "host": "192.0.2.1"} in dumped["claimed"]
    assert {"uid": "000000002", "host": "192.0.2.2"} in dumped["known"]
    assert dumped["recent_udp"] == []


@pytest.mark.asyncio
async def test_discovery_udp_ring_buffer() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._recent_udp.clear()

    service._process_datagram(
        b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone,iPower",
        ("10.0.0.90", 12107),
    )
    service._process_datagram(
        b"ASPort_12107,Mac_000025841,IP_10.0.0.91,iZone",
        ("10.0.0.91", 12107),
    )
    service._process_datagram(b"iZoneChanged_System", ("10.0.0.91", 12107))
    service._process_datagram(b"not-a-discovery-message", ("10.0.0.5", 12107))

    dumped = service.dump_state()
    assert len(dumped["recent_udp"]) == 4
    assert dumped["recent_udp"][0]["host"] == "10.0.0.90"
    assert dumped["recent_udp"][0]["tags"] == ["iZone", "iPower"]
    assert dumped["recent_udp"][1]["host"] == "10.0.0.91"
    assert dumped["recent_udp"][2]["message"] == "iZoneChanged_System"
    assert "uid" not in dumped["recent_udp"][2]
    assert dumped["recent_udp"][3]["message"] == "not-a-discovery-message"
    assert "T" in dumped["recent_udp"][0]["received_at"]

    for i in range(UDP_REPLY_BUFFER_SIZE + 5):
        service._process_datagram(
            f"ASPort_12107,Mac_000025841,IP_10.0.0.{i % 250},iZone".encode(),
            ("10.0.0.1", 12107),
        )
    assert len(service.dump_state()["recent_udp"]) == UDP_REPLY_BUFFER_SIZE

    await service.close()
