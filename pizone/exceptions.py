"""pizone-specific exceptions."""

from .const import PLACEHOLDER_DEVICE_UID


class ControllerCommandError(Exception):
    """The controller rejected or could not satisfy a request.

    Raised for ``{ERROR...}`` POST bodies and HTTP 4xx responses when the
    device is reachable. Not used for transport failures (timeouts, connection
    refused, etc.) — those remain :exc:`ConnectionError`.
    """


class ResponseDecodeError(ConnectionError):
    """The bridge responded over HTTP but the body could not be decoded as JSON.

    Raised when transport succeeds and the response is not a transport failure,
    but JSON parsing fails (and the ``{OK}`` suffix workaround does not apply).
    """


class UnpairedBridgeError(ValueError):
    """The device UID is the unpaired-bridge placeholder (``000000000``).

    Raised for intentional lookups/creates that target an unpaired ASH bridge.
    Passive discovery silently ignores this UID instead.
    """


def raise_if_placeholder_uid(uid: str) -> None:
    """Raise :exc:`UnpairedBridgeError` if *uid* is the unpaired placeholder."""
    if uid == PLACEHOLDER_DEVICE_UID:
        raise UnpairedBridgeError(
            f"Device UID {PLACEHOLDER_DEVICE_UID!r} is an unpaired ASH bridge "
            "(no paired AC); refuse to discover or create a controller"
        )
