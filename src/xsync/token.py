from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from xsync.config import XConfig

TOKEN_ENDPOINT = "https://api.x.com/2/oauth2/token"
AUTHORIZATION_ENDPOINT = "https://x.com/i/oauth2/authorize"


@dataclass(frozen=True)
class OAuthSession:
    authorization_url: str
    state: str
    code_verifier: str


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(token, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class TokenManager:
    def __init__(
        self,
        config: XConfig,
        store: TokenStore,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.http_client = http_client or httpx.Client(timeout=30.0)

    def create_oauth_session(self) -> OAuthSession:
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge_bytes = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(challenge_bytes).decode("ascii")
        challenge = challenge.rstrip("=")
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return OAuthSession(
            authorization_url=f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}",
            state=state,
            code_verifier=verifier,
        )

    def exchange_callback_url(
        self,
        callback_url: str,
        oauth_session: OAuthSession,
    ) -> dict[str, Any]:
        parsed = urlparse(callback_url)
        params = parse_qs(parsed.query)
        if "error" in params:
            description = params.get("error_description", ["Authorization failed"])[0]
            raise RuntimeError(description)
        returned_state = params.get("state", [None])[0]
        if returned_state != oauth_session.state:
            raise RuntimeError("OAuth state mismatch")
        code = params.get("code", [None])[0]
        if not code:
            raise RuntimeError("Callback URL did not include an authorization code")
        token = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": oauth_session.code_verifier,
                "client_id": self.config.client_id,
            }
        )
        self.store.save(token)
        return token

    def load_token(self) -> dict[str, Any]:
        token = self.store.load()
        if not token:
            raise FileNotFoundError(
                f"Missing token file at {self.store.path}. Run `xsync auth` first."
            )
        return token

    def ensure_access_token(self) -> dict[str, Any]:
        token = self.load_token()
        if _is_expired(token):
            token = self.refresh(token)
            self.store.save(token)
        return token

    def refresh(self, token: dict[str, Any]) -> dict[str, Any]:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("Token is expired and no refresh_token is available")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.client_id,
        }
        refreshed = self._token_request(payload)
        if "refresh_token" not in refreshed:
            refreshed["refresh_token"] = refresh_token
        return refreshed

    def _token_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth = None
        if self.config.client_secret:
            auth = (self.config.client_id, self.config.client_secret)
        response = self.http_client.post(
            TOKEN_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=auth,
        )
        response.raise_for_status()
        token = response.json()
        expires_in = int(token.get("expires_in", 0))
        if expires_in:
            token["expires_at"] = int(datetime.now(UTC).timestamp()) + expires_in
        return token


def wait_for_oauth_callback(redirect_uri: str, timeout_seconds: int = 180) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
        raise ValueError("redirect_uri must be a loopback http URL with an explicit port")

    callback_queue: Queue[str] = Queue(maxsize=1)
    expected_path = parsed.path or "/"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != expected_path:
                self.send_response(404)
                self.end_headers()
                return

            callback_queue.put(f"http://{parsed.netloc}{self.path}")
            body = b"Authorization received. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((parsed.hostname, parsed.port), Handler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        return callback_queue.get(timeout=timeout_seconds)
    finally:
        server.server_close()


def _is_expired(token: dict[str, Any], skew_seconds: int = 30) -> bool:
    expires_at = int(token.get("expires_at", 0))
    if not expires_at:
        return False
    return int(datetime.now(UTC).timestamp()) >= (expires_at - skew_seconds)
