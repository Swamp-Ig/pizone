"""Tests for controller HTTP GET/POST internals."""

from typing import cast

import aiohttp
from aiohttp import ClientSession
import pytest

from pizone import Controller, ControllerCommandError, ResponseDecodeError

from .conftest import MockDiscoveryService
from .http_fakes import FakeHttpResponse, FakeHttpSession


def _make_controller(service: MockDiscoveryService) -> Controller:
    controller = Controller.from_discovery(
        service,
        service._event_coordinator,
        device_uid="000000099",
        device_ip="10.0.0.99",
        is_v2=False,
        is_ipower=False,
    )
    controller._initialized = True
    return controller


@pytest.mark.asyncio
async def test_get_resource_happy_path(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(
            get_response=FakeHttpResponse(
                200,
                json_data={"SysOn": "on", "AirStreamDeviceUId": "000000099"},
            ),
        ),
    )
    try:
        result = await controller._get_resource("SystemSettings")
    finally:
        service._session = original_session

    assert result["SysOn"] == "on"
    assert controller.connected is True


@pytest.mark.asyncio
async def test_get_resource_http_404(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    controller._failed_connection(ConnectionError("Fake connection error"))
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_response=FakeHttpResponse(404, "404: File not found")),
    )
    try:
        with pytest.raises(ControllerCommandError):
            await controller._get_resource("SystemSettings")
    finally:
        service._session = original_session

    assert controller.connected is True


@pytest.mark.asyncio
async def test_get_resource_ok_suffix(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    body = '{"SysOn":"on","AirStreamDeviceUId":"000000099"}{OK}'
    service._session = cast(
        ClientSession,
        FakeHttpSession(
            get_response=FakeHttpResponse(200, body, json_error=True),
        ),
    )
    try:
        result = await controller._get_resource("SystemSettings")
    finally:
        service._session = original_session

    assert result["SysOn"] == "on"
    assert controller.connected is True


@pytest.mark.asyncio
async def test_get_resource_decode_failure(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(
            get_response=FakeHttpResponse(200, "not-json", json_error=True),
        ),
    )
    try:
        with pytest.raises(ResponseDecodeError):
            await controller._get_resource("SystemSettings")
    finally:
        service._session = original_session

    assert controller.bridge_connected is True
    assert controller.connected is True


@pytest.mark.asyncio
async def test_get_resource_no_session(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    service._session = None
    try:
        with pytest.raises(ConnectionError, match="Discovery service is not started"):
            await controller._get_resource("SystemSettings")
    finally:
        service._session = original_session


@pytest.mark.asyncio
async def test_get_resource_transport_error(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(get_error=aiohttp.ClientError("network down")),
    )
    try:
        with pytest.raises(ConnectionError):
            await controller._get_resource("SystemSettings")
    finally:
        service._session = original_session

    assert controller.connected is False


@pytest.mark.asyncio
async def test_send_command_http_500(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(post_response=FakeHttpResponse(500, "server error")),
    )
    try:
        with pytest.raises(ConnectionError):
            await controller._send_command_async("SystemMODE", {"SystemMODE": "cool"})
    finally:
        service._session = original_session

    assert controller.connected is False


@pytest.mark.asyncio
async def test_send_command_ok_suffix(service: MockDiscoveryService) -> None:
    controller = _make_controller(service)
    original_session = service._session
    service._session = cast(
        ClientSession,
        FakeHttpSession(post_response=FakeHttpResponse(200, "payload{OK}")),
    )
    try:
        result = await controller._send_command_async(
            "SystemMODE", {"SystemMODE": "cool"}
        )
    finally:
        service._session = original_session

    assert result == "payload"
    assert controller.connected is True
