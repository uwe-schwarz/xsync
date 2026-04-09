from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from xsync.config import RepoPaths


class ArchiveWriter:
    def __init__(self, paths: RepoPaths, http_client: httpx.Client | None = None) -> None:
        self.paths = paths
        self.http_client = http_client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.paths.ensure()

    def write_post(self, record: dict[str, Any]) -> Path:
        path = self.paths.posts_dir / f"{record['id']}.md"
        path.write_text(render_post_markdown(record), encoding="utf-8")
        self._download_media(record)
        return path

    def write_bookmark(self, bookmark: dict[str, Any], record: dict[str, Any]) -> Path:
        path = self.paths.bookmarks_dir / f"{record['id']}.md"
        path.write_text(render_bookmark_markdown(bookmark, record), encoding="utf-8")
        return path

    def write_thread(self, thread: dict[str, Any]) -> Path:
        path = self.paths.threads_dir / f"{thread['conversation_id']}.json"
        path.write_text(
            json.dumps(thread, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def write_run_manifest(self, timestamp: str, manifest: dict[str, Any]) -> Path:
        slug = timestamp.replace(":", "").replace("-", "")
        path = self.paths.runs_dir / f"{slug}.json"
        path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def _download_media(self, record: dict[str, Any]) -> None:
        for media in record.get("media", []):
            if media.get("type") == "photo":
                url = media.get("url")
                if url:
                    self._download_to_media_dir(url, str(media.get("media_key")))
            elif media.get("type") in {"video", "animated_gif"}:
                preview = media.get("preview_image_url")
                if preview:
                    self._download_to_media_dir(preview, f"{media.get('media_key')}-preview")

    def _download_to_media_dir(self, url: str, stem: str) -> Path | None:
        suffix = _suffix_for_url(url) or ".bin"
        destination = self.paths.media_dir / f"{stem}{suffix}"
        if destination.exists():
            return destination
        response = self.http_client.get(url)
        response.raise_for_status()
        destination.write_bytes(response.content)
        return destination


def render_post_markdown(record: dict[str, Any]) -> str:
    post = record["post"]
    author = record.get("author") or {}
    media_lines = (
        "\n".join(
            f"- `{media.get('media_key')}` `{media.get('type')}` alt={media.get('alt_text', '')!r}"
            for media in record.get("media", [])
        )
        or "- none"
    )
    referenced_lines = (
        "\n".join(
            f"- `{ref.get('type', 'unknown')}` -> `{ref.get('id')}`"
            for ref in post.get("referenced_tweets", [])
        )
        or "- none"
    )
    metrics = json.dumps(
        post.get("public_metrics", {}),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        f"---\n"
        f"id: {record['id']}\n"
        f"conversation_id: {record['conversation_id']}\n"
        f"url: {record['url']}\n"
        f"created_at: {post.get('created_at', '')}\n"
        f"author_username: {author.get('username', '')}\n"
        f"author_name: {author.get('name', '')}\n"
        f"---\n\n"
        f"{post.get('text', '').strip()}\n\n"
        f"## References\n"
        f"{referenced_lines}\n\n"
        f"## Media\n"
        f"{media_lines}\n\n"
        f"## Public Metrics\n"
        f"```json\n{metrics}\n```\n"
    )


def render_bookmark_markdown(bookmark: dict[str, Any], record: dict[str, Any]) -> str:
    bookmark_meta = bookmark["bookmark"]
    return (
        f"---\n"
        f"post_id: {bookmark['post_id']}\n"
        f"thread_id: {bookmark['thread_id']}\n"
        f"first_seen_bookmarked_at: {bookmark['first_seen_bookmarked_at']}\n"
        f"last_seen_bookmarked_at: {bookmark['last_seen_bookmarked_at']}\n"
        f"removed_at: {bookmark['removed_at'] or ''}\n"
        f"post_path: x-posts/{record['id']}.md\n"
        f"thread_path: x-threads/{bookmark['thread_id']}.json\n"
        f"---\n\n"
        f"Canonical post: `x-posts/{record['id']}.md`\n\n"
        f"Thread: `x-threads/{bookmark['thread_id']}.json`\n\n"
        f"Observed source payload:\n"
        f"```json\n{json.dumps(bookmark_meta, indent=2, ensure_ascii=False, sort_keys=True)}\n```\n"
    )


def build_post_record(post: dict[str, Any], includes: dict[str, Any] | None) -> dict[str, Any]:
    includes = includes or {}
    users_by_id = {str(user["id"]): user for user in includes.get("users", [])}
    media_by_key = {str(media["media_key"]): media for media in includes.get("media", [])}
    polls_by_id = {str(poll["id"]): poll for poll in includes.get("polls", [])}
    places_by_id = {str(place["id"]): place for place in includes.get("places", [])}
    referenced_by_id = {str(tweet["id"]): tweet for tweet in includes.get("tweets", [])}

    attachments = post.get("attachments", {})
    media_keys = attachments.get("media_keys", [])
    poll_ids = attachments.get("poll_ids", [])
    record = {
        "id": str(post["id"]),
        "conversation_id": str(post.get("conversation_id") or post["id"]),
        "author_id": post.get("author_id"),
        "url": _post_url(users_by_id.get(str(post.get("author_id"))), str(post["id"])),
        "post": post,
        "author": users_by_id.get(str(post.get("author_id"))),
        "media": [media_by_key[key] for key in media_keys if key in media_by_key],
        "polls": [polls_by_id[poll_id] for poll_id in poll_ids if poll_id in polls_by_id],
        "place": places_by_id.get(str(post.get("geo", {}).get("place_id"))),
        "in_reply_to_user": users_by_id.get(str(post.get("in_reply_to_user_id"))),
        "referenced_posts": [
            referenced_by_id[str(ref["id"])]
            for ref in post.get("referenced_tweets", [])
            if str(ref.get("id")) in referenced_by_id
        ],
        "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    return record


def build_thread_document(
    conversation_id: str,
    posts: list[dict[str, Any]],
    missing_post_ids: list[str],
) -> dict[str, Any]:
    ordered = sorted(posts, key=lambda item: (item["post"].get("created_at", ""), item["id"]))
    return {
        "conversation_id": conversation_id,
        "root_post_id": conversation_id,
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "posts": ordered,
        "missing_post_ids": sorted(set(missing_post_ids)),
    }


def _post_url(author: dict[str, Any] | None, post_id: str) -> str:
    username = "i"
    if author and author.get("username"):
        username = str(author["username"])
    return f"https://x.com/{username}/status/{post_id}"


def _suffix_for_url(url: str) -> str | None:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix
    return suffix.lower() if suffix else None
