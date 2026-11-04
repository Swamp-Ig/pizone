"""Shared public types."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
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


class VerifyFailure(StrEnum):
    """Why an address failed the HTTP check."""

    UNRESOLVED = "unresolved"
    """The host name didn't resolve to any IPv4 address."""
    NO_REPLY = "no_reply"
    """No HTTP response from the address."""
    UNREADABLE = "unreadable"
    """An HTTP response, but not usable controller settings."""
    OTHER_UID = "other_uid"
    """A controller answered, but with a different UID."""


_FAILURE_TEXT = {
    VerifyFailure.UNRESOLVED: "host name did not resolve",
    VerifyFailure.NO_REPLY: "no HTTP reply",
    VerifyFailure.UNREADABLE: "unreadable HTTP reply",
}


def _failure_text(failure: VerifyFailure, answered_uid: str | None) -> str:
    """Plain-English reason, for logs and exception messages."""
    if failure is VerifyFailure.OTHER_UID:
        return f"answered as UID {answered_uid}"
    return _FAILURE_TEXT[failure]


@dataclass(frozen=True, slots=True)
class UnverifiedEndpoint:
    """A UID seen over UDP whose address failed the HTTP check."""

    endpoint: ControllerEndpoint
    """UID and address from the UDP reply."""
    failure: VerifyFailure
    answered_uid: str | None
    """The UID that answered instead; set only for ``OTHER_UID``."""
    at: datetime
    """When the check failed, in UTC."""

    def __str__(self) -> str:
        """Plain-English description, for logs."""
        reason = _failure_text(self.failure, self.answered_uid)
        return f"iZone controller {self.endpoint.uid} at {self.endpoint.host}: {reason}"


class DiscoveredEndpoints(list[ControllerEndpoint]):
    """Endpoints verified by one ``discover_all()`` call.

    A list, so existing iteration keeps working. :attr:`unverified` holds the
    endpoints seen during that same call that failed the HTTP check.
    """

    def __init__(
        self,
        endpoints: Iterable[ControllerEndpoint] = (),
        *,
        unverified: Iterable[UnverifiedEndpoint] = (),
    ) -> None:
        """Create the result from verified and unverified endpoints."""
        super().__init__(endpoints)
        self.unverified: tuple[UnverifiedEndpoint, ...] = tuple(unverified)
