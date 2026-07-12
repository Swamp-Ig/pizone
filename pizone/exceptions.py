"""pizone-specific exceptions."""


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
