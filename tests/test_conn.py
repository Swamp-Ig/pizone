
import asyncio
from asyncio import AbstractEventLoop
from typing import Any

import aiohttp
from aiohttp import ClientSession

device_ip = '10.0.0.107'

async def send_command_async(command: str, data: Any):
    body = {command : data}
    url = f"http://{device_ip}/{command}"
    async with ClientSession() as session:
        async with session.post(url, json=body) as response:
            response.raise_for_status()

loop: AbstractEventLoop = asyncio.get_event_loop()
loop.run_until_complete(loop.create_task(send_command_async('SystemMODE', 'heat')))

# Demonstrates how aiohttp is broken :(
# see: https://github.com/aio-libs/aiohttp/issues/3208
