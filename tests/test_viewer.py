from __future__ import annotations

import json

from xsync.viewer import ArchiveIndex, _parse_frontmatter, _parse_markdown_post_sections


def test_parse_frontmatter_and_sections() -> None:
    content = """---
id: 123
conversation_id: 123
url: https://x.com/example/status/123
created_at: 2026-04-10T11:00:00.000Z
author_username: example
author_name: Example User
---

Hello world
https://example.com

## References
- `quoted` -> `555`

## Media
- `3_999` `photo` alt='Test \\u2019 image'

## Public Metrics
```json
{"like_count": 3, "reply_count": 1}
```
"""
    frontmatter, body = _parse_frontmatter(content)
    text, references, media, metrics = _parse_markdown_post_sections(body)

    assert frontmatter["id"] == "123"
    assert text == "Hello world\nhttps://example.com"
    assert references == [{"type": "quoted", "target_id": "555"}]
    assert media == [{"media_key": "3_999", "type": "photo", "alt_text": "Test ’ image"}]
    assert metrics == {"like_count": 3, "reply_count": 1}


def test_archive_index_builds_cards_and_thread_details(tmp_path) -> None:
    archive_root = tmp_path / "archive"
    for name in ("x-posts", "x-bookmarks", "x-threads", "x-media"):
        (archive_root / name).mkdir(parents=True)

    (archive_root / "x-media" / "3_abc.jpg").write_bytes(b"jpg")

    (archive_root / "x-posts" / "100.md").write_text(
        """---
id: 100
conversation_id: 100
url: https://x.com/example/status/100
created_at: 2026-04-10T11:00:00.000Z
author_username: example
author_name: Example User
---

Root post

## References
- none

## Media
- `3_abc` `photo` alt='Preview'

## Public Metrics
```json
{"like_count": 8, "reply_count": 1, "bookmark_count": 2}
```
""",
        encoding="utf-8",
    )
    (archive_root / "x-posts" / "101.md").write_text(
        """---
id: 101
conversation_id: 100
url: https://x.com/example/status/101
created_at: 2026-04-10T11:10:00.000Z
author_username: example
author_name: Example User
---

Reply post

## References
- `replied_to` -> `100`

## Media
- none

## Public Metrics
```json
{"like_count": 4, "reply_count": 0, "bookmark_count": 1}
```
""",
        encoding="utf-8",
    )
    (archive_root / "x-bookmarks" / "100.md").write_text(
        """---
post_id: 100
thread_id: 100
first_seen_bookmarked_at: 2026-04-10T11:30:00Z
last_seen_bookmarked_at: 2026-04-10T11:30:00Z
removed_at:
post_path: x-posts/100.md
thread_path: x-threads/100.json
---
""",
        encoding="utf-8",
    )
    (archive_root / "x-threads" / "100.json").write_text(
        json.dumps(
            {
                "conversation_id": "100",
                "root_post_id": "100",
                "missing_post_ids": [],
                "posts": [
                    {
                        "id": "100",
                        "conversation_id": "100",
                        "url": "https://x.com/example/status/100",
                        "author": {
                            "name": "Example User",
                            "username": "example",
                            "profile_image_url": "https://example.com/avatar.jpg",
                            "verified": True,
                        },
                        "media": [
                            {
                                "media_key": "3_abc",
                                "type": "photo",
                                "url": "https://example.com/photo.jpg",
                                "width": 1200,
                                "height": 800,
                            }
                        ],
                        "post": {
                            "created_at": "2026-04-10T11:00:00.000Z",
                            "text": "Root post",
                            "public_metrics": {
                                "like_count": 8,
                                "reply_count": 1,
                                "bookmark_count": 2,
                                "retweet_count": 0,
                                "quote_count": 0,
                                "impression_count": 50,
                            },
                        },
                    },
                    {
                        "id": "101",
                        "conversation_id": "100",
                        "url": "https://x.com/example/status/101",
                        "author": {
                            "name": "Example User",
                            "username": "example",
                            "profile_image_url": "https://example.com/avatar.jpg",
                            "verified": True,
                        },
                        "media": [],
                        "post": {
                            "created_at": "2026-04-10T11:10:00.000Z",
                            "text": "Reply post",
                            "public_metrics": {
                                "like_count": 4,
                                "reply_count": 0,
                                "bookmark_count": 1,
                                "retweet_count": 0,
                                "quote_count": 0,
                                "impression_count": 20,
                            },
                            "referenced_tweets": [{"id": "100", "type": "replied_to"}],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = ArchiveIndex(archive_root).snapshot()

    assert snapshot.stats["posts"] == 2
    assert snapshot.stats["bookmarks"] == 1
    assert snapshot.posts[0]["id"] == "101"
    assert snapshot.details_by_id["100"]["post"]["bookmarked"] is True
    assert snapshot.details_by_id["100"]["post"]["media"][0]["localUrl"] == "/media/3_abc.jpg"
    assert snapshot.details_by_id["101"]["post"]["replyToPostId"] == "100"
    assert snapshot.details_by_id["101"]["thread"]["size"] == 2
