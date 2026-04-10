from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from xdk import Client

from xsync.config import XConfig
from xsync.token import TokenManager

TWEET_FIELDS = [
    "attachments",
    "author_id",
    "conversation_id",
    "created_at",
    "edit_history_tweet_ids",
    "entities",
    "id",
    "in_reply_to_user_id",
    "lang",
    "public_metrics",
    "referenced_tweets",
    "reply_settings",
    "source",
    "text",
]
EXPANSIONS = [
    "attachments.media_keys",
    "attachments.poll_ids",
    "author_id",
    "geo.place_id",
    "in_reply_to_user_id",
    "referenced_tweets.id",
    "referenced_tweets.id.attachments.media_keys",
    "referenced_tweets.id.author_id",
]
MEDIA_FIELDS = [
    "alt_text",
    "duration_ms",
    "height",
    "media_key",
    "preview_image_url",
    "type",
    "url",
    "variants",
    "width",
]
USER_FIELDS = [
    "created_at",
    "description",
    "id",
    "location",
    "name",
    "pinned_tweet_id",
    "profile_image_url",
    "protected",
    "public_metrics",
    "url",
    "username",
    "verified",
]
PLACE_FIELDS = [
    "contained_within",
    "country",
    "country_code",
    "full_name",
    "geo",
    "id",
    "name",
    "place_type",
]


class XApi:
    def __init__(
        self,
        config: XConfig,
        token_manager: TokenManager,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.token_manager = token_manager
        self.usage = Counter()
        self.progress = progress or (lambda _: None)

    def get_me(self) -> dict[str, Any]:
        self.usage["users.get_me"] += 1
        return self._request_json(
            path="/2/users/me",
            params={"user.fields": ",".join(USER_FIELDS)},
            auth_mode="oauth2",
            usage_label="users.get_me",
        )

    def search_all(
        self,
        *,
        query: str,
        since_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        params = {
            "query": query,
            "max_results": 500,
            "tweet.fields": ",".join(TWEET_FIELDS),
            "expansions": ",".join(EXPANSIONS),
            "media.fields": ",".join(MEDIA_FIELDS),
            "user.fields": ",".join(USER_FIELDS),
            "place.fields": ",".join(PLACE_FIELDS),
        }
        if since_id is not None:
            params["since_id"] = since_id
        pagination_token: str | None = None
        while True:
            page_params = dict(params)
            if pagination_token:
                page_params["pagination_token"] = pagination_token
            page = self._request_json(
                path="/2/tweets/search/all",
                params=page_params,
                auth_mode="bearer",
                usage_label="posts.search_all",
                progress_label=f"posts.search_all query={query!r}",
            )
            self.usage["posts.search_all"] += 1
            yield page
            pagination_token = _next_token(page)
            if not pagination_token:
                break

    def get_user_posts(
        self,
        user_id: str,
        *,
        since_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        params = {
            "max_results": 100,
            "exclude": "replies,reposts",
            "tweet.fields": ",".join(TWEET_FIELDS),
            "expansions": ",".join(EXPANSIONS),
            "media.fields": ",".join(MEDIA_FIELDS),
            "user.fields": ",".join(USER_FIELDS),
            "place.fields": ",".join(PLACE_FIELDS),
        }
        if since_id is not None:
            params["since_id"] = since_id
        pagination_token: str | None = None
        while True:
            page_params = dict(params)
            if pagination_token:
                page_params["pagination_token"] = pagination_token
            page = self._request_json(
                path=f"/2/users/{user_id}/tweets",
                params=page_params,
                auth_mode="oauth2",
                usage_label="users.get_posts",
                progress_label=f"users.get_posts user_id={user_id}",
            )
            self.usage["users.get_posts"] += 1
            yield page
            pagination_token = _next_token(page)
            if not pagination_token:
                break

    def get_bookmarks(self, user_id: str) -> Iterator[dict[str, Any]]:
        params = {
            "max_results": 100,
            "tweet.fields": ",".join(TWEET_FIELDS),
            "expansions": ",".join(EXPANSIONS),
            "media.fields": ",".join(MEDIA_FIELDS),
            "user.fields": ",".join(USER_FIELDS),
            "place.fields": ",".join(PLACE_FIELDS),
        }
        pagination_token: str | None = None
        while True:
            page_params = dict(params)
            if pagination_token:
                page_params["pagination_token"] = pagination_token
            page = self._request_json(
                path=f"/2/users/{user_id}/bookmarks",
                params=page_params,
                auth_mode="oauth2",
                usage_label="users.get_bookmarks",
                progress_label=f"users.get_bookmarks user_id={user_id}",
            )
            self.usage["users.get_bookmarks"] += 1
            yield page
            pagination_token = _next_token(page)
            if not pagination_token:
                break

    def get_posts_by_ids(self, ids: list[str]) -> dict[str, Any]:
        if not ids:
            return {"data": []}
        self.usage["posts.get_by_ids"] += 1
        return self._request_json(
            path="/2/tweets",
            params={
                "ids": ",".join(ids),
                "tweet.fields": ",".join(TWEET_FIELDS),
                "expansions": ",".join(EXPANSIONS),
                "media.fields": ",".join(MEDIA_FIELDS),
                "user.fields": ",".join(USER_FIELDS),
                "place.fields": ",".join(PLACE_FIELDS),
            },
            auth_mode="bearer",
            usage_label="posts.get_by_ids",
        )

    def _client(self) -> Client:
        token = self.token_manager.ensure_access_token()
        bearer_token = self.config.bearer_token or token["access_token"]
        return Client(
            bearer_token=bearer_token,
            access_token=token["access_token"],
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            redirect_uri=self.config.redirect_uri,
            scope=list(self.config.scopes),
            token=token,
        )

    def _request_json(
        self,
        *,
        path: str,
        params: dict[str, Any],
        auth_mode: str,
        usage_label: str,
        progress_label: str | None = None,
        max_attempts: int = 6,
    ) -> dict[str, Any]:
        client = self._client()
        url = client.base_url + path
        headers = _auth_headers(client, auth_mode)
        label = progress_label or usage_label
        for attempt in range(1, max_attempts + 1):
            response = client.session.get(url, params=params, headers=headers)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()

            if attempt == max_attempts:
                response.raise_for_status()

            delay = _retry_delay_seconds(response.headers, now=time.time(), attempt=attempt)
            reset_at = _reset_at_text(response.headers, delay_seconds=delay)
            self.progress(
                f"Rate limited on {label}; waiting {delay:.0f}s before retrying"
                + (f" (until {reset_at})" if reset_at else "")
            )
            time.sleep(delay)

        raise RuntimeError(f"Failed to fetch {label}")


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value


def _next_token(page: dict[str, Any]) -> str | None:
    meta = page.get("meta")
    if isinstance(meta, dict):
        token = meta.get("next_token")
        return str(token) if token else None
    return None


def _auth_headers(client: Client, auth_mode: str) -> dict[str, str]:
    if auth_mode == "oauth2":
        if not client.access_token:
            raise ValueError("OAuth2 user token required but not available")
        return {"Authorization": f"Bearer {client.access_token}"}

    bearer_token = client.bearer_token or client.access_token
    if not bearer_token:
        raise ValueError("Bearer token required but not available")
    return {"Authorization": f"Bearer {bearer_token}"}


def _retry_delay_seconds(headers: Any, *, now: float, attempt: int) -> float:
    retry_after = _header_value(headers, "retry-after")
    if retry_after:
        try:
            return max(float(retry_after), 1.0)
        except ValueError:
            retry_at = _parse_http_date(retry_after)
            if retry_at is not None:
                return max(retry_at - now, 1.0)

    reset = _header_value(headers, "x-rate-limit-reset")
    if reset:
        try:
            return max(float(reset) - now + 1.0, 1.0)
        except ValueError:
            pass

    return min(float(2**attempt), 60.0)


def _reset_at_text(headers: Any, *, delay_seconds: float) -> str | None:
    reset = _header_value(headers, "x-rate-limit-reset")
    if reset:
        try:
            moment = datetime.fromtimestamp(float(reset), tz=UTC)
            return moment.strftime("%H:%M:%SZ")
        except (OverflowError, ValueError):
            return None

    retry_after = _header_value(headers, "retry-after")
    retry_at = _parse_http_date(retry_after) if retry_after else None
    if retry_at is None:
        return None
    moment = datetime.fromtimestamp(retry_at, tz=UTC)
    return moment.strftime("%H:%M:%SZ")


def _header_value(headers: Any, key: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(key)
        if value is None:
            value = headers.get(key.title())
        if value is None:
            value = headers.get(key.upper())
        return None if value is None else str(value)
    return None


def _parse_http_date(value: str) -> float | None:
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
