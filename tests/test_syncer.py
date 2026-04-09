from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from xsync.config import RepoPaths
from xsync.exporter import ArchiveWriter
from xsync.store import StateStore
from xsync.syncer import SyncService


@dataclass
class FakeApi:
    usage: dict[str, int] = field(default_factory=dict)

    def search_all(self, *, query: str, since_id: str | None = None):  # noqa: ANN202
        self.usage["posts.search_all"] = self.usage.get("posts.search_all", 0) + 1
        if query == "from:alice -is:reply -is:retweet":
            yield {
                "data": [
                    {
                        "id": "200",
                        "author_id": "user-1",
                        "conversation_id": "200",
                        "created_at": "2026-04-09T10:00:00Z",
                        "text": "authored post",
                        "public_metrics": {"like_count": 1},
                    }
                ],
                "includes": {
                    "users": [{"id": "user-1", "username": "alice", "name": "Alice"}],
                },
            }
            return
        if query == "conversation_id:100 from:replier":
            yield {
                "data": [
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
                    {
                        "id": "102",
                        "author_id": "other-2",
                        "conversation_id": "100",
                        "created_at": "2026-04-08T09:06:00Z",
                        "text": "follow-up post",
                        "referenced_tweets": [{"id": "101", "type": "replied_to"}],
                        "public_metrics": {"like_count": 4},
                    },
                ],
                "includes": {
                    "users": [{"id": "other-2", "username": "replier", "name": "Replier"}],
                    "media": [
                        {
                            "media_key": "media-1",
                            "type": "photo",
                            "url": "https://cdn.example.com/image.jpg",
                            "alt_text": "a picture",
                        }
                    ],
                },
            }
            return
        yield {
            "data": [
                {
                    "id": "100",
                    "author_id": "other-1",
                    "conversation_id": "100",
                    "created_at": "2026-04-08T09:00:00Z",
                    "text": "root post",
                    "public_metrics": {"like_count": 2},
                },
                {
                    "id": "101",
                    "author_id": "other-2",
                    "conversation_id": "100",
                    "created_at": "2026-04-08T09:05:00Z",
                    "text": "reply post",
                    "referenced_tweets": [{"id": "100", "type": "replied_to"}],
                    "public_metrics": {"like_count": 3},
                },
            ],
            "includes": {
                "users": [
                    {"id": "other-1", "username": "rooter", "name": "Rooter"},
                    {"id": "other-2", "username": "replier", "name": "Replier"},
                ]
            },
        }

    def get_bookmarks(self, user_id: str):  # noqa: ANN202
        self.usage["users.get_bookmarks"] = self.usage.get("users.get_bookmarks", 0) + 1
        yield {
            "data": [
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
            "includes": {
                "users": [{"id": "other-2", "username": "replier", "name": "Replier"}],
                "media": [
                    {
                        "media_key": "media-1",
                        "type": "photo",
                        "url": "https://cdn.example.com/image.jpg",
                        "alt_text": "a picture",
                    }
                ],
            },
        }

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

    posts_result = syncer.sync_posts("alice")
    bookmarks_result = syncer.sync_bookmarks("user-1")

    assert posts_result.counts["posts_upserted"] == 1
    assert bookmarks_result.counts["bookmarks_seen"] == 1
    assert (tmp_path / "x-posts" / "200.md").exists()
    assert (tmp_path / "x-posts" / "101.md").exists()
    assert (tmp_path / "x-posts" / "102.md").exists()
    assert not (tmp_path / "x-posts" / "100.md").exists()
    assert (tmp_path / "x-bookmarks" / "101.md").exists()
    assert (tmp_path / "x-threads" / "100.json").exists()
    assert (tmp_path / "x-media" / "media-1.jpg").exists()
    assert "posts.get_by_ids" not in bookmarks_result.usage


def test_sync_reports_progress(tmp_path: Path) -> None:
    paths = RepoPaths.from_root(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"image-bytes"))
    writer = ArchiveWriter(paths, http_client=httpx.Client(transport=transport))
    store = StateStore(paths.state_db)
    api = FakeApi()
    messages: list[str] = []
    syncer = SyncService(api, store, writer, progress=messages.append)

    syncer.sync_posts("alice")
    syncer.sync_bookmarks("user-1")

    assert any(message.startswith("Syncing original posts") for message in messages)
    assert any(message.startswith("Posts progress:") for message in messages)
    assert any(message.startswith("Syncing bookmarks") for message in messages)
    assert any(message.startswith("Hydrating bookmark thread") for message in messages)
