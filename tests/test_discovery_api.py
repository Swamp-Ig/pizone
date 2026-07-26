"""Tests for the pizone 1.4 discovery API."""

# disposition: 1.4 | deprecate  (untagged = keep)
#   keep      — default; no tag required. Shared dual-track / pathway-agnostic tests.
#   1.4       — new consumer-driven discovery / refresh API
#   deprecate — legacy track; grep and delete when dual-track ends
#               (sticky within a function until the next disposition tag).

import asyncio
import errno
import sys
from typing import cast
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession
import pytest

from pizone import (
    Controller,
    ControllerAlreadyClaimedError,
    ControllerCommandError,
    ControllerEndpoint,
    UnpairedBridgeError,
    create_discovery,
)
from pizone.const import PLACEHOLDER_DEVICE_UID
from pizone.discovery import DiscoveryService

from .conftest import MockController, MockDiscoveryService
from .http_fakes import FakeHttpResponse, FakeHttpSession

discovery_module = sys.modules["pizone.discovery"]


def _system_settings(uid: str) -> dict[str, object]:
    return {
        "AirStreamDeviceUId": uid,
        "SysOn": "on",
        "SysMode": "heat",
        "SysFan": "auto",
        "NoOfZones": 0,
        "FanAuto": "disabled",
    }


def _system_settings_response(uid: str) -> FakeHttpResponse:
    return FakeHttpResponse(200, json_data=_system_settings(uid))


def _probe_result(uid: str, host: str) -> tuple[ControllerEndpoint, dict[str, object]]:
    return ControllerEndpoint(uid=uid, host=host), _system_settings(uid)


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_discovery_singleton() -> None:
    """create_discovery is one-shot and close clears the global."""
    with patch.object(
        DiscoveryService,
        "start_discovery",
        AsyncMock(),
    ):
        disco = await create_discovery()
        assert disco is discovery_module._active_discovery
        with pytest.raises(RuntimeError, match="already created"):
            await create_discovery()
        await disco.close()
    assert discovery_module._active_discovery is None


# disposition: 1.4
@pytest.mark.asyncio
async def test_close_then_immediate_create_discovery_rebinds() -> None:
    """close() must release UDP :7005 before returning so a new create can bind.

    Reproduces the delete→HomeKit race: stop discovery then immediately start
    again on the same event loop without awaiting an extra sleep.

    Use an injected session (as Home Assistant does) so close() does not await
    an owned ClientSession — that await can mask the teardown race.
    """
    session = ClientSession()
    try:
        first = await create_discovery(session=session)
        await first.close()
        assert discovery_module._active_discovery is None

        second = await create_discovery(session=session)
        try:
            assert second is not first
            assert discovery_module._active_discovery is second
        finally:
            await second.close()
        assert discovery_module._active_discovery is None
    finally:
        leftover = discovery_module._active_discovery
        if leftover is not None:
            await leftover.close()
        await session.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_owned_session_close_still_needs_udp_teardown_wait() -> None:
    """Owned-session await must not be treated as sufficient UDP teardown.

    CPython asyncio schedules ``DatagramTransport`` sock close via
    ``call_soon(_call_connection_lost)``. The next ``await`` (owned
    ``ClientSession.close``) usually runs that callback first — FIFO
    ``call_soon`` behaviour, not a language/compiler guarantee. Defer the
    callback so only waiting on ``connection_lost`` (Fix A) makes rebind safe.
    """
    loop = asyncio.get_running_loop()
    real_call_soon = loop.call_soon
    teardown_delay = 0.05

    def call_soon(callback: object, *args: object, context: object = None) -> object:
        if getattr(callback, "__name__", None) == "_call_connection_lost":
            return loop.call_later(teardown_delay, callback, *args)
        if context is not None:
            return real_call_soon(callback, *args, context=context)
        return real_call_soon(callback, *args)

    first = await create_discovery()
    try:
        with patch.object(loop, "call_soon", call_soon):
            await first.close()
        assert discovery_module._active_discovery is None

        second = await create_discovery()
        try:
            assert second is not first
            assert discovery_module._active_discovery is second
        finally:
            await second.close()
        assert discovery_module._active_discovery is None
    finally:
        await asyncio.sleep(teardown_delay + 0.01)
        leftover = discovery_module._active_discovery
        if leftover is not None:
            await leftover.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_discovery_failed_start_allows_retry() -> None:
    """Failed start_discovery must not leave the process singleton set.

    Otherwise later create_discovery calls raise RuntimeError until process
    restart (HomeKit rediscovery / SETUP_RETRY poisoned after EADDRINUSE).
    """
    try:
        with (
            patch.object(
                DiscoveryService,
                "start_discovery",
                AsyncMock(
                    side_effect=OSError(errno.EADDRINUSE, "Address already in use")
                ),
            ),
            pytest.raises(OSError, match="Address already in use"),
        ):
            await create_discovery()

        assert discovery_module._active_discovery is None

        with patch.object(DiscoveryService, "start_discovery", AsyncMock()):
            disco = await create_discovery()
            try:
                assert disco is discovery_module._active_discovery
            finally:
                await disco.close()
        assert discovery_module._active_discovery is None
    finally:
        leftover = discovery_module._active_discovery
        if leftover is not None:
            await leftover.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_failed_start_holds_singleton_until_close_finishes() -> None:
    """Overlapping create during failed-start cleanup must hit already-created.

    ``create_discovery`` must not clear the process singleton before ``close()``
    finishes, so a client that serializes create→close→create cannot bind
    against a service still tearing down.
    """
    original_close = DiscoveryService.close
    close_entered = asyncio.Event()
    finish_close = asyncio.Event()
    held_during_close: list[bool] = []

    async def gated_close(self: DiscoveryService) -> None:
        held_during_close.append(discovery_module._active_discovery is self)
        close_entered.set()
        await finish_close.wait()
        await original_close(self)

    try:
        with (
            patch.object(
                DiscoveryService,
                "start_discovery",
                AsyncMock(
                    side_effect=OSError(errno.EADDRINUSE, "Address already in use")
                ),
            ),
            patch.object(DiscoveryService, "close", gated_close),
        ):
            create_task = asyncio.create_task(create_discovery())
            await asyncio.wait_for(close_entered.wait(), timeout=1)
            assert discovery_module._active_discovery is not None
            with pytest.raises(RuntimeError, match="already created"):
                await create_discovery()
            finish_close.set()
            with pytest.raises(OSError, match="Address already in use"):
                await create_task

        assert held_during_close == [True]
        assert discovery_module._active_discovery is None
    finally:
        finish_close.set()
        leftover = discovery_module._active_discovery
        if leftover is not None:
            await leftover.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_host() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_host_unreachable() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=OSError("unreachable")),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint is None
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            FakeHttpResponse(200, "<html>not a bridge</html>", json_error=True),
            id="html",
        ),
        pytest.param(
            FakeHttpResponse(200, json_data={"SysOn": "on"}),
            id="json-missing-uid",
        ),
        pytest.param(
            FakeHttpResponse(200, json_data=None),
            id="empty-null-body",
        ),
        pytest.param(
            FakeHttpResponse(200, json_data=["not", "an", "object"]),
            id="json-array",
        ),
        pytest.param(
            FakeHttpResponse(200, json_data="not-an-object"),
            id="json-string",
        ),
        pytest.param(
            FakeHttpResponse(200, "garbage{OK}", json_error=True),
            id="ok-suffix-non-json",
        ),
        pytest.param(
            FakeHttpResponse(200, unicode_error=True),
            id="non-utf8",
        ),
    ],
)
async def test_discover_by_host_malformed_body_returns_none(
    response: FakeHttpResponse,
) -> None:
    """Manual host widens _probe input; content failures must not raise."""
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(ClientSession, FakeHttpSession(get_response=response))
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint is None
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_host_ok_suffix_json() -> None:
    """Bridge quirk: JSON body with a trailing {OK} still probes successfully."""
    body = '{"AirStreamDeviceUId":"000025841","SysOn":"on"}{OK}'
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(
            get_response=FakeHttpResponse(200, body, json_error=True),
        ),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_host_uses_known_cache() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.discover_by_host("10.0.0.90")
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=OSError("should not probe")),
    )
    endpoint = await service.discover_by_host("10.0.0.90")
    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_host_raises_if_controller_exists() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=OSError("should not probe")),
    )
    with pytest.raises(ControllerAlreadyClaimedError, match="already created"):
        await service.discover_by_host("10.0.0.90")
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_uid() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )

    async def scan_and_reply() -> None:
        service._process_datagram(
            b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
            ("10.0.0.90", 12107),
        )

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", side_effect=scan_and_reply),
    ):
        endpoint = await service.discover_by_uid("000025841")

    assert endpoint == ControllerEndpoint(uid="000025841", host="10.0.0.90")
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_uid_raises_if_controller_exists() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    scan = AsyncMock()
    with (
        patch.object(service, "scan", scan),
        pytest.raises(ControllerAlreadyClaimedError, match="already created"),
    ):
        await service.discover_by_uid("000025841")
    scan.assert_not_awaited()
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_all_invokes_callback() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    async def scan_and_reply() -> None:
        service._process_datagram(
            b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
            ("10.0.0.90", 12107),
        )

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", side_effect=scan_and_reply),
    ):
        endpoints = await service.discover_all()

    assert endpoints == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    assert discovered == endpoints
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_all_notifies_known_without_new_datagram() -> None:
    """User scan notifies once for already-known same-host (no wait-window ASPort)."""
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    service._known_endpoints["000025841"] = ControllerEndpoint(
        uid="000025841", host="10.0.0.90"
    )
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", AsyncMock()),
    ):
        endpoints = await service.discover_all()

    assert endpoints == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    assert discovered == endpoints
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_all_dedupes_udp_and_verify_notify() -> None:
    """ASPort during wait + verify must not double-fire on_endpoint_discovered."""
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    async def scan_and_reply() -> None:
        service._process_datagram(
            b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
            ("10.0.0.90", 12107),
        )

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", side_effect=scan_and_reply),
    ):
        await service.discover_all()

    assert discovered == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_passive_asport_notifies_new_only() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append
    datagram = b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone"

    service._process_datagram(datagram, ("10.0.0.90", 12107))
    service._process_datagram(datagram, ("10.0.0.90", 12107))

    assert discovered == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_passive_asport_notifies_on_host_change() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    service._process_datagram(
        b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
        ("10.0.0.90", 12107),
    )
    service._process_datagram(
        b"ASPort_12107,Mac_000025841,IP_10.0.0.91,iZone",
        ("10.0.0.91", 12107),
    )

    assert discovered == [
        ControllerEndpoint(uid="000025841", host="10.0.0.90"),
        ControllerEndpoint(uid="000025841", host="10.0.0.91"),
    ]
    assert service._known_endpoints["000025841"].host == "10.0.0.91"
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_claimed_asport_host_change_fires_on_address_changed() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    seen: list[ControllerEndpoint] = []
    controller = await service.create_controller(
        "000025841",
        "10.0.0.90",
        on_address_changed=seen.append,
    )

    service._process_datagram(
        b"ASPort_12107,Mac_000025841,IP_10.0.0.91,iZone",
        ("10.0.0.91", 12107),
    )
    await asyncio.sleep(0)

    assert controller.device_ip == "10.0.0.91"
    assert seen == [ControllerEndpoint(uid="000025841", host="10.0.0.91")]
    assert service._claimed_endpoints["000025841"].host == "10.0.0.91"
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_claimed_asport_same_host_silent() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    seen: list[ControllerEndpoint] = []
    controller = await service.create_controller(
        "000025841",
        "10.0.0.90",
        on_address_changed=seen.append,
    )

    service._process_datagram(
        b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
        ("10.0.0.90", 12107),
    )
    await asyncio.sleep(0)

    assert controller.device_ip == "10.0.0.90"
    assert seen == []
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_close_does_not_fire_on_endpoint_discovered() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append
    controller = await service.create_controller("000025841", "10.0.0.90")
    await controller.close()

    assert discovered == []
    assert "000025841" in service._known_endpoints
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_changed_datagrams_ignored_on_14_path() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    discovered: list[ControllerEndpoint] = []
    service._on_endpoint_discovered = discovered.append

    service._process_datagram(b"iZoneChanged_System", ("10.0.0.90", 12107))
    service._process_datagram(b"iZoneChanged_Zones", ("10.0.0.90", 12107))
    service._process_datagram(b"iZoneChanged_Schedules", ("10.0.0.90", 12107))

    assert discovered == []
    assert service._known_endpoints == {}
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_calls_are_serialized() -> None:
    """Concurrent discover_all calls must not overlap shared scratch state."""
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    active = 0
    overlapped = False

    async def scan_and_reply() -> None:
        nonlocal active, overlapped
        active += 1
        if active > 1:
            overlapped = True
        service._process_datagram(
            b"ASPort_12107,Mac_000025841,IP_10.0.0.90,iZone",
            ("10.0.0.90", 12107),
        )
        await asyncio.sleep(0)
        active -= 1

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock(side_effect=asyncio.sleep)),
        patch.object(service, "scan", side_effect=scan_and_reply),
    ):
        first, second = await asyncio.gather(
            service.discover_all(), service.discover_all()
        )

    expected = [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    assert first == expected
    assert second == expected
    assert overlapped is False
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_all_excludes_created_controllers() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")

    async def _scan_reports_claimed() -> None:
        assert service._scan_collector is not None
        service._scan_collector["000025841"] = "10.0.0.90"

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", side_effect=_scan_reports_claimed),
    ):
        endpoints = await service.discover_all()
    assert endpoints == []
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_all_includes_closed_controllers() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    controller = await service.create_controller("000025841", "10.0.0.90")
    await controller.close()
    assert service._known_endpoints["000025841"] == ControllerEndpoint(
        uid="000025841", host="10.0.0.90"
    )
    assert "000025841" not in service._claimed_endpoints
    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", AsyncMock()),
    ):
        endpoints = await service.discover_all()
    assert endpoints == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_scan_sends_broadcast() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    await service.start_discovery()
    with patch.object(service, "_send_broadcasts") as send_broadcasts:
        await service.scan()
    send_broadcasts.assert_called_once()
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_controller_success() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    session = FakeHttpSession(get_response=_system_settings_response("000025841"))
    service._session = cast(ClientSession, session)
    controller = await service.create_controller("000025841", "10.0.0.90")
    assert controller.device_uid == "000025841"
    assert controller.device_ip == "10.0.0.90"
    assert service._claimed_endpoints["000025841"] == ControllerEndpoint(
        uid="000025841", host="10.0.0.90"
    )
    assert "000025841" not in service._known_endpoints
    assert session.get_calls == 1
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_controller_raises_if_uid_exists() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    await service.create_controller("000025841", "10.0.0.90")
    with pytest.raises(ControllerAlreadyClaimedError, match="already created"):
        await service.create_controller("000025841", "10.0.0.90")
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_controller_address_fallback() -> None:
    service = MockDiscoveryService(legacy_pathway=False)

    responses = {
        "10.0.0.1": _system_settings_response("000099999"),
        "10.0.0.90": _system_settings_response("000025841"),
    }

    class RoutingSession(FakeHttpSession):
        def get(self, url: object, **_kwargs: object) -> FakeHttpResponse:
            ip = str(url).split("//", 1)[1].split("/", 1)[0]
            return responses[ip]

    service._session = cast(ClientSession, RoutingSession())
    discover_calls = 0

    async def discover_by_uid_patched(uid: str) -> ControllerEndpoint | None:
        del uid
        nonlocal discover_calls
        discover_calls += 1
        return ControllerEndpoint(uid="000025841", host="10.0.0.90")

    with patch.object(service, "discover_by_uid", side_effect=discover_by_uid_patched):
        controller = await service.create_controller("000025841", "10.0.0.1")

    assert discover_calls == 1
    assert controller.device_ip == "10.0.0.90"
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_controller_address_changed_after_return() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    seen: list[ControllerEndpoint] = []
    returned: Controller | None = None

    async def discover_by_uid(_uid: str) -> ControllerEndpoint | None:
        return ControllerEndpoint(uid="000025841", host="10.0.0.90")

    with (
        patch.object(service, "discover_by_uid", side_effect=discover_by_uid),
        patch.object(
            service,
            "_probe",
            AsyncMock(
                side_effect=[
                    None,
                    _probe_result("000025841", "10.0.0.90"),
                ]
            ),
        ),
        patch.object(
            MockController,
            "_initialize",
            AsyncMock(),
        ),
    ):
        returned = await service.create_controller(
            "000025841",
            "10.0.0.1",
            on_address_changed=seen.append,
        )

    assert returned is not None
    assert returned.device_uid == "000025841"
    await asyncio.sleep(0)
    assert seen == [ControllerEndpoint(uid="000025841", host="10.0.0.90")]
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_controller_no_retry_on_command_error() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response("000025841")),
    )
    discover_by_uid = AsyncMock()
    with (
        patch.object(service, "discover_by_uid", discover_by_uid),
        patch.object(
            Controller,
            "_initialize",
            AsyncMock(side_effect=ControllerCommandError("rejected")),
        ),
        pytest.raises(ControllerCommandError),
    ):
        await service.create_controller("000025841", "10.0.0.90")
    discover_by_uid.assert_not_awaited()
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_udp_ignores_placeholder_uid() -> None:
    """Passive ASPort for the unpaired placeholder must not cache or notify."""
    discovered: list[ControllerEndpoint] = []
    service = MockDiscoveryService(legacy_pathway=False)
    service._on_endpoint_discovered = discovered.append
    service._process_datagram(
        b"ASPort_12107,Mac_000000000,IP_10.0.0.111,iZone",
        ("10.0.0.111", 12107),
    )
    assert discovered == []
    assert service._known_endpoints == {}
    await service.close()


# disposition: deprecate
@pytest.mark.asyncio
async def test_legacy_udp_ignores_placeholder_uid() -> None:
    """Legacy pathway must not initialize a controller for the placeholder UID."""
    service = MockDiscoveryService(legacy_pathway=True)
    service._process_datagram(
        b"ASPort_12107,Mac_000000000,IP_10.0.0.111,iZone",
        ("10.0.0.111", 12107),
    )
    await asyncio.sleep(0)
    assert service._controllers == {}
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_uid_placeholder_raises_without_io() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    scan = AsyncMock()
    with (
        patch.object(service, "scan", scan),
        pytest.raises(UnpairedBridgeError, match="unpaired"),
    ):
        await service.discover_by_uid(PLACEHOLDER_DEVICE_UID)
    scan.assert_not_awaited()
    assert service._known_endpoints == {}
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_create_controller_placeholder_raises_without_io() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    probe = AsyncMock()
    with (
        patch.object(service, "_probe", probe),
        pytest.raises(UnpairedBridgeError, match="unpaired"),
    ):
        await service.create_controller(PLACEHOLDER_DEVICE_UID, "10.0.0.111")
    probe.assert_not_awaited()
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_by_host_placeholder_raises_without_cache() -> None:
    service = MockDiscoveryService(legacy_pathway=False)
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response(PLACEHOLDER_DEVICE_UID)),
    )
    with pytest.raises(UnpairedBridgeError, match="unpaired"):
        await service.discover_by_host("10.0.0.111")
    assert service._known_endpoints == {}
    await service.close()


# disposition: 1.4
@pytest.mark.asyncio
async def test_discover_all_omits_placeholder_uid() -> None:
    discovered: list[ControllerEndpoint] = []
    service = MockDiscoveryService(legacy_pathway=False)
    service._on_endpoint_discovered = discovered.append
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=_system_settings_response(PLACEHOLDER_DEVICE_UID)),
    )

    async def scan_and_reply() -> None:
        # Inject into collector as if UDP had been accepted before filtering;
        # discover_all must still omit after probe.
        assert service._scan_collector is not None
        service._scan_collector[PLACEHOLDER_DEVICE_UID] = "10.0.0.111"

    with (
        patch("pizone.discovery.asyncio.sleep", AsyncMock()),
        patch.object(service, "scan", AsyncMock(side_effect=scan_and_reply)),
    ):
        endpoints = await service.discover_all()
    assert endpoints == []
    assert discovered == []
    assert service._known_endpoints == {}
    await service.close()
