from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
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
    def __init__(self, config: XConfig, token_manager: TokenManager) -> None:
        self.config = config
        self.token_manager = token_manager
        self.usage = Counter()

    def get_me(self) -> dict[str, Any]:
        client = self._client()
        self.usage["users.get_me"] += 1
        response = client.users.get_me(user_fields=USER_FIELDS)
        return _model_dump(response)

    def search_all(
        self,
        *,
        query: str,
        since_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        client = self._client()
        iterator = client.posts.search_all(
            query=query,
            since_id=since_id,
            max_results=100,
            tweet_fields=TWEET_FIELDS,
            expansions=EXPANSIONS,
            media_fields=MEDIA_FIELDS,
            user_fields=USER_FIELDS,
            place_fields=PLACE_FIELDS,
        )
        for page in iterator:
            self.usage["posts.search_all"] += 1
            yield _model_dump(page)

    def get_bookmarks(self, user_id: str) -> Iterator[dict[str, Any]]:
        client = self._client()
        iterator = client.users.get_bookmarks(
            user_id,
            max_results=100,
            tweet_fields=TWEET_FIELDS,
            expansions=EXPANSIONS,
            media_fields=MEDIA_FIELDS,
            user_fields=USER_FIELDS,
            place_fields=PLACE_FIELDS,
        )
        for page in iterator:
            self.usage["users.get_bookmarks"] += 1
            yield _model_dump(page)

    def get_posts_by_ids(self, ids: list[str]) -> dict[str, Any]:
        if not ids:
            return {"data": []}
        client = self._client()
        self.usage["posts.get_by_ids"] += 1
        response = client.posts.get_by_ids(
            ids,
            tweet_fields=TWEET_FIELDS,
            expansions=EXPANSIONS,
            media_fields=MEDIA_FIELDS,
            user_fields=USER_FIELDS,
            place_fields=PLACE_FIELDS,
        )
        return _model_dump(response)

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


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value
