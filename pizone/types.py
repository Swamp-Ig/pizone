"""Shared types for the 1.4 API."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ControllerEndpoint:
    """Discovered controller network address."""

    uid: str
    host: str


@dataclass(frozen=True)
class ControllerProbe:
    """HTTP probe result used to create a controller.

    Distinct from :class:`ControllerEndpoint`: discovery caches only uid/host.
    Connect needs the initial settings snapshot and which read wire to use.
    """

    endpoint: ControllerEndpoint
    system_settings: dict[str, Any]
    read_api: Literal["v1", "v2"] = "v1"
    v2_use_content_type: bool | None = None
