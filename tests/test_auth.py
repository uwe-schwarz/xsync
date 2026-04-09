from __future__ import annotations

from pathlib import Path

import httpx

from xsync.config import XConfig
from xsync.token import OAuthSession, TokenManager, TokenStore


def test_exchange_callback_url_persists_token(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.x.com/2/oauth2/token")
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    manager = TokenManager(
        XConfig(
            client_id="client-id",
            client_secret=None,
            redirect_uri="http://127.0.0.1:8787/callback",
            scopes=("tweet.read", "users.read"),
        ),
        TokenStore(tmp_path / "token.json"),
        http_client=httpx.Client(transport=transport),
    )
    oauth_session = OAuthSession(
        authorization_url="https://x.com/i/oauth2/authorize?state=abc",
        state="abc",
        code_verifier="verifier",
    )
    token = manager.exchange_callback_url(
        "http://127.0.0.1:8787/callback?state=abc&code=xyz",
        oauth_session,
    )
    assert token["access_token"] == "access"
    assert (tmp_path / "token.json").exists()
