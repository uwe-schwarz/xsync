from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from xsync.config import RepoPaths
from xsync.exporter import ArchiveWriter, build_thread_document
from xsync.store import StateStore
from xsync.syncer import SyncService


def _page(data: list[dict[str, Any]], includes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"data": data, "includes": includes or {}}


def _user(user_id: str, username: str, name: str) -> dict[str, str]:
    return {"id": user_id, "username": username, "name": name}


@dataclass
class FakeApi:
    usage: dict[str, int] = field(default_factory=dict)
    search_queries: list[str] = field(default_factory=list)
    search_since_ids: list[tuple[str, str | None]] = field(default_factory=list)
    user_post_since_ids: list[str | None] = field(default_factory=list)
    bookmark_page_reads: int = 0
    run: int = 1

    def search_all(self, *, query: str, since_id: str | None = None):  # noqa: ANN202
        self.usage["posts.search_all"] = self.usage.get("posts.search_all", 0) + 1
        self.search_queries.append(query)
        self.search_since_ids.append((query, since_id))
        if query == "from:alice -is:reply -is:retweet":
            yield _page(
                [
                    {
                        "id": "200",
                        "author_id": "user-1",
                        "conversation_id": "200",
                        "created_at": "2026-04-09T10:00:00Z",
                        "text": "authored post",
                        "public_metrics": {"like_count": 1},
                    }
                ],
                {"users": [_user("user-1", "alice", "Alice")]},
            )
            return
        if query == "conversation_id:100 in_reply_to_tweet_id:101 from:replier":
            yield _page(
                [
                    {
                        "id": "102",
                        "author_id": "other-2",
                        "conversation_id": "100",
                        "created_at": "2026-04-08T09:06:00Z",
                        "text": "follow-up post",
                        "referenced_tweets": [{"id": "101", "type": "replied_to"}],
                        "public_metrics": {"like_count": 4},
                    }
                ],
                {
                    "users": [_user("other-2", "replier", "Replier")],
                    "media": [
                        {
                            "media_key": "media-1",
                            "type": "photo",
                            "url": "https://cdn.example.com/image.jpg",
                            "alt_text": "a picture",
                        }
                    ],
                },
            )
            return
        if query == "conversation_id:100 in_reply_to_tweet_id:102 from:replier":
            yield _page([])
            return
        if query == "conversation_id:100 in_reply_to_tweet_id:103 from:replier":
            yield _page(
                [
                    {
                        "id": "104",
                        "author_id": "other-2",
                        "conversation_id": "100",
                        "created_at": "2026-04-10T09:08:00Z",
                        "text": "new continuation",
                        "referenced_tweets": [{"id": "103", "type": "replied_to"}],
                        "public_metrics": {"like_count": 6},
                    }
                ],
                {"users": [_user("other-2", "replier", "Replier")]},
            )
            return
        if query == "conversation_id:100 in_reply_to_tweet_id:104 from:replier":
            yield _page([])
            return
        raise AssertionError(f"Unexpected search_all query: {query}")

    def get_user_posts(self, user_id: str, *, since_id: str | None = None):  # noqa: ANN202
        self.usage["users.get_posts"] = self.usage.get("users.get_posts", 0) + 1
        self.user_post_since_ids.append(since_id)
        if self.run == 2:
            yield _page(
                [
                    {
                        "id": "201",
                        "author_id": user_id,
                        "conversation_id": "201",
                        "created_at": "2026-04-10T08:00:00Z",
                        "text": "new authored post",
                        "public_metrics": {"like_count": 2},
                    }
                ],
                {"users": [_user(user_id, "alice", "Alice")]},
            )
            return
        yield _page([], {"users": [_user(user_id, "alice", "Alice")]})

    def get_bookmarks(self, user_id: str):  # noqa: ANN202
        self.usage["users.get_bookmarks"] = self.usage.get("users.get_bookmarks", 0) + 1
        if self.run == 1:
            self.bookmark_page_reads += 1
            yield _page(
                [
                    {
                        "id": "101",
                        "author_id": "other-2",
                        "conversation_id": "100",
                        "created_at": "2026-04-08T09:05:00Z",
                        "text": "reply post",
                        "attachments": {"media_keys": ["media-1"]},
                        "referenced_tweets": [{"id": "100", "type": "replied_to"}],
                        "public_metrics": {"like_count": 3},
                    }
                ],
                {
                    "users": [_user("other-2", "replier", "Replier")],
                    "media": [
                        {
                            "media_key": "media-1",
                            "type": "photo",
                            "url": "https://cdn.example.com/image.jpg",
                            "alt_text": "a picture",
                        }
                    ],
                },
            )
            return

        self.bookmark_page_reads += 1
        yield _page(
            [
                {
                    "id": "103",
                    "author_id": "other-2",
                    "conversation_id": "100",
                    "created_at": "2026-04-10T09:07:00Z",
                    "text": "new bookmarked reply",
                    "referenced_tweets": [{"id": "102", "type": "replied_to"}],
                    "public_metrics": {"like_count": 5},
                },
                {
                    "id": "101",
                    "author_id": "other-2",
                    "conversation_id": "100",
                    "created_at": "2026-04-08T09:05:00Z",
                    "text": "reply post",
                    "attachments": {"media_keys": ["media-1"]},
                    "referenced_tweets": [{"id": "100", "type": "replied_to"}],
                    "public_metrics": {"like_count": 3},
                },
            ],
            {"users": [_user("other-2", "replier", "Replier")]},
        )
        self.bookmark_page_reads += 1
        yield _page(
            [
                {
                    "id": "100",
                    "author_id": "other-1",
                    "conversation_id": "100",
                    "created_at": "2026-04-08T09:00:00Z",
                    "text": "root post",
                    "public_metrics": {"like_count": 2},
                }
            ],
            {"users": [_user("other-1", "rooter", "Rooter")]},
        )

    def get_posts_by_ids(self, ids: list[str]) -> dict[str, Any]:
        self.usage["posts.get_by_ids"] = self.usage.get("posts.get_by_ids", 0) + 1
        return {"data": []}


def test_sync_posts_and_bookmarks(tmp_path: Path) -> None:
    paths = RepoPaths.from_root(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"image-bytes"))
    writer = ArchiveWriter(paths, http_client=httpx.Client(transport=transport))
    store = StateStore(paths.state_db)
    api = FakeApi()
    syncer = SyncService(api, store, writer)

    posts_result = syncer.sync_posts("user-1", "alice")
    bookmarks_result = syncer.sync_bookmarks("user-1")

    assert posts_result.counts["posts_upserted"] == 1
    assert bookmarks_result.counts["bookmarks_seen"] == 1
    assert (tmp_path / "x-posts" / "200.md").exists()
    assert (tmp_path / "x-posts" / "101.md").exists()
    assert (tmp_path / "x-posts" / "102.md").exists()
    assert not (tmp_path / "x-posts" / "103.md").exists()
    assert not (tmp_path / "x-posts" / "100.md").exists()
    assert (tmp_path / "x-bookmarks" / "101.md").exists()
    assert (tmp_path / "x-threads" / "100.json").exists()
    assert (tmp_path / "x-media" / "media-1.jpg").exists()
    assert "posts.get_by_ids" not in bookmarks_result.usage
    assert "conversation_id:100 from:replier" not in api.search_queries
    assert "conversation_id:100 in_reply_to_tweet_id:101 from:replier" in api.search_queries


def test_incremental_sync_only_fetches_new_posts_and_bookmarks(tmp_path: Path) -> None:
    paths = RepoPaths.from_root(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"image-bytes"))
    writer = ArchiveWriter(paths, http_client=httpx.Client(transport=transport))
    store = StateStore(paths.state_db)
    api = FakeApi()
    syncer = SyncService(api, store, writer)

    syncer.sync_posts("user-1", "alice")
    syncer.sync_bookmarks("user-1")

    api.run = 2
    api.bookmark_page_reads = 0

    posts_result = syncer.sync_posts("user-1", "alice")
    bookmarks_result = syncer.sync_bookmarks("user-1")

    assert posts_result.counts["posts_upserted"] == 1
    assert posts_result.usage["users.get_posts"] == 1
    assert "posts.search_all" not in posts_result.usage
    assert api.user_post_since_ids == ["200"]
    assert (tmp_path / "x-posts" / "201.md").exists()

    assert bookmarks_result.counts["bookmarks_seen"] == 1
    assert bookmarks_result.counts["bookmarks_removed"] == 0
    assert api.bookmark_page_reads == 1
    assert (tmp_path / "x-posts" / "103.md").exists()
    assert (tmp_path / "x-posts" / "104.md").exists()
    assert (tmp_path / "x-bookmarks" / "103.md").exists()

    assert api.search_queries.count("from:alice -is:reply -is:retweet") == 1
    query_101 = "conversation_id:100 in_reply_to_tweet_id:101 from:replier"
    query_102 = "conversation_id:100 in_reply_to_tweet_id:102 from:replier"
    query_103 = "conversation_id:100 in_reply_to_tweet_id:103 from:replier"
    assert api.search_queries.count(query_101) == 1
    assert api.search_queries.count(query_102) == 1
    assert api.search_queries.count(query_103) == 1


def test_sync_bookmarks_reuses_cached_seed_threads_without_requerying(tmp_path: Path) -> None:
    @dataclass
    class CachedSeedApi(FakeApi):
        def get_bookmarks(self, user_id: str):  # noqa: ANN202
            self.usage["users.get_bookmarks"] = self.usage.get("users.get_bookmarks", 0) + 1
            self.bookmark_page_reads += 1
            yield _page(
                [
                    {
                        "id": "103",
                        "author_id": "other-2",
                        "conversation_id": "100",
                        "created_at": "2026-04-10T09:07:00Z",
                        "text": "new bookmarked reply",
                        "referenced_tweets": [{"id": "102", "type": "replied_to"}],
                        "public_metrics": {"like_count": 5},
                    }
                ],
                {"users": [_user("other-2", "replier", "Replier")]},
            )

    paths = RepoPaths.from_root(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"image-bytes"))
    writer = ArchiveWriter(paths, http_client=httpx.Client(transport=transport))
    store = StateStore(paths.state_db)
    api = CachedSeedApi(run=2)
    syncer = SyncService(api, store, writer)

    cached_seed = {
        "id": "103",
        "author_id": "other-2",
        "conversation_id": "100",
        "created_at": "2026-04-10T09:07:00Z",
        "url": "https://x.com/replier/status/103",
        "author": _user("other-2", "replier", "Replier"),
        "post": {
            "id": "103",
            "author_id": "other-2",
            "conversation_id": "100",
            "created_at": "2026-04-10T09:07:00Z",
            "text": "new bookmarked reply",
            "referenced_tweets": [{"id": "102", "type": "replied_to"}],
            "public_metrics": {"like_count": 5},
        },
        "media": [],
    }
    cached_continuation = {
        "id": "104",
        "author_id": "other-2",
        "conversation_id": "100",
        "created_at": "2026-04-10T09:08:00Z",
        "url": "https://x.com/replier/status/104",
        "author": _user("other-2", "replier", "Replier"),
        "post": {
            "id": "104",
            "author_id": "other-2",
            "conversation_id": "100",
            "created_at": "2026-04-10T09:08:00Z",
            "text": "new continuation",
            "referenced_tweets": [{"id": "103", "type": "replied_to"}],
            "public_metrics": {"like_count": 6},
        },
        "media": [],
    }

    for record in (cached_seed, cached_continuation):
        store.upsert_post(record, "thread", "2026-04-10T09:09:00Z")
    store.upsert_thread(
        "100",
        build_thread_document("100", [cached_seed, cached_continuation], []),
        "2026-04-10T09:09:00Z",
    )
    store.set_sync_state("thread_parent_since_id:100:103", "104")

    bookmarks_result = syncer.sync_bookmarks("user-1")

    assert bookmarks_result.counts["bookmarks_seen"] == 1
    assert (tmp_path / "x-bookmarks" / "103.md").exists()
    assert "conversation_id:100 in_reply_to_tweet_id:103 from:replier" not in api.search_queries


def test_sync_reports_progress(tmp_path: Path) -> None:
    paths = RepoPaths.from_root(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"image-bytes"))
    writer = ArchiveWriter(paths, http_client=httpx.Client(transport=transport))
    store = StateStore(paths.state_db)
    api = FakeApi()
    messages: list[str] = []
    syncer = SyncService(api, store, writer, progress=messages.append)

    syncer.sync_posts("user-1", "alice")
    syncer.sync_bookmarks("user-1")

    assert any(message.startswith("Syncing original posts") for message in messages)
    assert any(message.startswith("Posts progress:") for message in messages)
    assert any(message.startswith("Syncing bookmarks") for message in messages)
    assert any(message.startswith("Hydrating bookmark thread") for message in messages)
