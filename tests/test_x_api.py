from __future__ import annotations

import time
from typing import Any

import requests

from xsync.config import DEFAULT_SCOPES, XConfig
from xsync.x_api import XApi


class FakeTokenManager:
    def ensure_access_token(self) -> dict[str, str]:
        return {"access_token": "user-token"}


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, session: FakeSession) -> None:
        self.base_url = "https://api.x.com"
        self.session = session
        self.bearer_token = "app-bearer"
        self.access_token = "user-token"


def test_search_all_retries_after_rate_limit(monkeypatch) -> None:
    now = time.time()
    responses = [
        FakeResponse(
            status_code=429,
            headers={"x-rate-limit-reset": str(int(now) + 2)},
        ),
        FakeResponse(
            status_code=200,
            json_data={"data": [{"id": "1"}], "meta": {}},
        ),
    ]
    session = FakeSession(responses)
    api = XApi(
        XConfig(
            client_id="client-id",
            client_secret=None,
            redirect_uri="http://127.0.0.1",
            scopes=DEFAULT_SCOPES,
        ),
        FakeTokenManager(),
    )
    api._client = lambda: FakeClient(session)  # type: ignore[method-assign]
    sleeps: list[float] = []
    messages: list[str] = []
    api.progress = messages.append
    monkeypatch.setattr("xsync.x_api.time.sleep", sleeps.append)

    pages = list(api.search_all(query="from:alice", since_id=None))

    assert pages == [{"data": [{"id": "1"}], "meta": {}}]
    assert api.usage["posts.search_all"] == 1
    assert len(session.calls) == 2
    assert sleeps and sleeps[0] >= 1.0
    assert any("Rate limited on posts.search_all" in message for message in messages)


def test_get_bookmarks_uses_oauth_token() -> None:
    session = FakeSession(
        [FakeResponse(status_code=200, json_data={"data": [], "meta": {}})]
    )
    api = XApi(
        XConfig(
            client_id="client-id",
            client_secret=None,
            redirect_uri="http://127.0.0.1",
            scopes=DEFAULT_SCOPES,
        ),
        FakeTokenManager(),
    )
    api._client = lambda: FakeClient(session)  # type: ignore[method-assign]

    pages = list(api.get_bookmarks("123"))

    assert pages == [{"data": [], "meta": {}}]
    assert session.calls[0]["headers"]["Authorization"] == "Bearer user-token"
