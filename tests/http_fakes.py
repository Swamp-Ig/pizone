"""Fake aiohttp session/response objects for controller HTTP tests."""

import json
from types import TracebackType


class FakeHttpResponse:
    """Minimal async HTTP response double."""

    def __init__(
        self,
        status: int,
        body: str = "",
        *,
        json_data: dict[str, object] | None = None,
        json_error: bool = False,
    ) -> None:
        self.status = status
        self.reason = "Not Found" if status == 404 else "Internal Server Error" if status == 500 else "OK"
        self._body = body
        self._json_data = json_data
        self._json_error = json_error

    async def json(self, content_type: str | None = None) -> dict[str, object]:
        del content_type
        if self._json_error:
            raise json.JSONDecodeError("invalid", self._body, 0)
        if self._json_data is not None:
            return self._json_data
        return json.loads(self._body)

    async def text(self, encoding: str | None = None) -> str:
        del encoding
        return self._body

    async def __aenter__(self) -> FakeHttpResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeHttpSession:
    """Minimal aiohttp ClientSession double."""

    def __init__(
        self,
        response: FakeHttpResponse | None = None,
        *,
        get_response: FakeHttpResponse | None = None,
        post_response: FakeHttpResponse | None = None,
        get_error: Exception | None = None,
        post_error: Exception | None = None,
    ) -> None:
        self._get_response = get_response if get_response is not None else response
        self._post_response = post_response if post_response is not None else response
        self._get_error = get_error
        self._post_error = post_error
        self.get_calls = 0
        self.post_calls = 0

    def get(self, *_args: object, **_kwargs: object) -> FakeHttpResponse:
        self.get_calls += 1
        if self._get_error is not None:
            raise self._get_error
        assert self._get_response is not None
        return self._get_response

    def post(self, *_args: object, **_kwargs: object) -> FakeHttpResponse:
        self.post_calls += 1
        if self._post_error is not None:
            raise self._post_error
        assert self._post_response is not None
        return self._post_response

    async def close(self) -> None:
        return None
