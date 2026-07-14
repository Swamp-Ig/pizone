"""Shared types for the 1.4 API."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ControllerEndpoint:
    """Discovered controller network address."""

    uid: str
    host: str
