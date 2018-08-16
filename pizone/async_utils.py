"""Utilities for asynchonous IO"""

from threading import Thread
from typing import Callable, Optional
from asyncio import AbstractEventLoop
from aiohttp import ClientSession

_EVENT_LOOP: Optional[AbstractEventLoop] = None

def set_event_loop(loop: AbstractEventLoop) -> None:
    """Set the event loop for the package.
    This must be called prior to any call to any package function
    raises:
        RuntimeError: If the event loop has already been set (to a different value).
    """
    global _EVENT_LOOP
    if _EVENT_LOOP is loop:
        return
    if _EVENT_LOOP:
        raise RuntimeError("Event loop already set up for package.")
    _EVENT_LOOP = loop

def get_event_loop() -> AbstractEventLoop:
    """Get the event loop for the package. Will create one and run in a thread if needed."""
    global _EVENT_LOOP
    if not _EVENT_LOOP:
        import asyncio
        _EVENT_LOOP = asyncio.new_event_loop()
        def start_worker(loop):
            """Switch to new event loop and run forever"""
            asyncio.set_event_loop(loop)
            loop.run_forever()
        # Create the new loop and worker thread
        worker = Thread(target=start_worker, args=(_EVENT_LOOP,), daemon=True)
        worker.start()

    return _EVENT_LOOP

_CLIENT_SESSION: Optional[ClientSession] = None
def set_client_session(session) -> None:
    """Set the event loop for the package.
    This must be called prior to any call to any package function
    raises:
        RuntimeError: If the event loop has already been set (to a different value).
    """
    global _CLIENT_SESSION
    if _CLIENT_SESSION is loop:
        return
    if _CLIENT_SESSION:
        raise RuntimeError("Event loop already set up for package.")
    _CLIENT_SESSION = loop

def get_client_session() -> ClientSession:
    """Get the event loop for the package. If none set up using set_client_session, will use the default event loop."""
    global _CLIENT_SESSION
    if not _CLIENT_SESSION:
        _CLIENT_SESSION = ClientSession(loop=get_event_loop())
    return _CLIENT_SESSION

