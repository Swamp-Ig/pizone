"""Bounded UDP shutdown when a Proactor transport never reports connection_lost."""
import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientSession
import pytest

from pizone.discovery import DiscoveryService


@pytest.mark.asyncio
async def test_stuck_transport_aborts_and_finishes_all_cleanup() -> None:
    """A failed UDP close cannot strand tasks, the session or concurrent closers."""
    service = DiscoveryService()
    transport = MagicMock(spec=asyncio.DatagramTransport)
    service._transport = cast(asyncio.DatagramTransport, transport)
    session = AsyncMock(spec=ClientSession)
    service._session = cast(ClientSession, session)
    task = service.create_task(asyncio.sleep(3600))
    with patch('pizone.discovery.TRANSPORT_CLOSE_TIMEOUT', 0.01):
        await asyncio.wait_for(asyncio.gather(service.close(), service.close()), 0.5)
    transport.close.assert_called_once()
    transport.abort.assert_called_once()
    session.close.assert_awaited_once()
    assert task.cancelled()
    assert service._transport is None
    assert service.is_closed


@pytest.mark.asyncio
async def test_normal_connection_lost_does_not_abort_transport() -> None:
    """Normal asynchronous socket release retains its existing graceful path."""
    service = DiscoveryService()
    transport = MagicMock(spec=asyncio.DatagramTransport)
    service._transport = cast(asyncio.DatagramTransport, transport)
    transport.close.side_effect = lambda: asyncio.get_running_loop().call_soon(service._on_connection_lost, None)
    await asyncio.wait_for(service.close(), 0.5)
    transport.close.assert_called_once()
    transport.abort.assert_not_called()
    assert service._transport is None
