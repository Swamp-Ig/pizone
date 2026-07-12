"""pizone-specific exceptions."""


class ControllerCommandError(Exception):
    """The controller rejected or could not satisfy a request.

    Raised for ``{ERROR...}`` POST bodies and HTTP 4xx responses when the
    device is reachable. Not used for transport failures (timeouts, connection
    refused, etc.) — those remain :exc:`ConnectionError`.
    """
