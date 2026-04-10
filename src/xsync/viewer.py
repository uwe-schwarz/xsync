from __future__ import annotations

import ast
import errno
import json
import os
import re
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import unquote, urlparse

import typer

ARCHIVE_ENV_VAR = "XSYNC_ARCHIVE_ROOT"
DEFAULT_VIEWER_PORT = 8123
PORT_SCAN_LIMIT = 25
_MARKDOWN_MEDIA_RE = re.compile(
    r"^- `(?P<media_key>[^`]+)` `(?P<media_type>[^`]+)` alt=(?P<alt>.+)$"
)
_REFERENCE_RE = re.compile(r"^- `(?P<kind>[^`]+)` -> `(?P<target_id>[^`]+)`$")


@dataclass(frozen=True)
class ArchiveSnapshot:
    archive_root: str
    generated_at: str
    posts: list[dict[str, Any]]
    details_by_id: dict[str, dict[str, Any]]
    stats: dict[str, Any]


class ArchiveIndex:
    def __init__(self, archive_root: Path) -> None:
        self.archive_root = archive_root
        self._lock = Lock()
        self._signature: tuple[tuple[int, int], ...] | None = None
        self._snapshot: ArchiveSnapshot | None = None

    @cached_property
    def paths(self) -> dict[str, Path]:
        return {
            "posts": self.archive_root / "x-posts",
            "bookmarks": self.archive_root / "x-bookmarks",
            "threads": self.archive_root / "x-threads",
            "media": self.archive_root / "x-media",
        }

    def snapshot(self) -> ArchiveSnapshot:
        signature = tuple(_directory_signature(path) for path in self.paths.values())
        with self._lock:
            if self._snapshot is None or signature != self._signature:
                self._snapshot = self._build_snapshot()
                self._signature = signature
            return self._snapshot

    def _build_snapshot(self) -> ArchiveSnapshot:
        bookmarks = _load_bookmarks(self.paths["bookmarks"])
        threads = _load_threads(self.paths["threads"])
        markdown_posts = _load_markdown_posts(self.paths["posts"])
        conversation_sizes = _conversation_sizes(markdown_posts, threads)

        post_cards: dict[str, dict[str, Any]] = {}
        details_by_id: dict[str, dict[str, Any]] = {}
        authors: set[str] = set()
        media_count = 0

        for post_id, markdown_post in markdown_posts.items():
            thread = threads.get(markdown_post["conversation_id"])
            thread_post = _find_thread_post(thread, post_id)
            card = _build_post_card(
                markdown_post=markdown_post,
                thread_post=thread_post,
                thread=thread,
                bookmarks=bookmarks,
                media_dir=self.paths["media"],
                conversation_sizes=conversation_sizes,
            )
            post_cards[post_id] = card
            author_key = card["author"].get("username") or card["author"].get("name")
            if author_key:
                authors.add(str(author_key))
            media_count += len(card["media"])

        for post_id, markdown_post in markdown_posts.items():
            thread = threads.get(markdown_post["conversation_id"])
            details_by_id[post_id] = _build_post_detail(
                post_id=post_id,
                markdown_post=markdown_post,
                thread=thread,
                bookmarks=bookmarks,
                media_dir=self.paths["media"],
                post_cards=post_cards,
                conversation_sizes=conversation_sizes,
            )

        posts = sorted(
            post_cards.values(),
            key=lambda item: (item["createdAt"], item["id"]),
            reverse=True,
        )
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        stats = {
            "posts": len(posts),
            "bookmarks": len(bookmarks),
            "threads": len(threads),
            "authors": len(authors),
            "media": media_count,
        }
        return ArchiveSnapshot(
            archive_root=str(self.archive_root),
            generated_at=generated_at,
            posts=posts,
            details_by_id=details_by_id,
            stats=stats,
        )


class ViewerServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, archive_index: ArchiveIndex) -> None:
        super().__init__((host, port), ViewerRequestHandler)
        self.archive_index = archive_index
        self.host = host


class ViewerRequestHandler(BaseHTTPRequestHandler):
    server_version = "xsync-viewer/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_asset("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._serve_asset("app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._serve_asset("styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/api/posts":
            snapshot = self.server.archive_index.snapshot()  # type: ignore[attr-defined]
            self._send_json(
                {
                    "archiveRoot": snapshot.archive_root,
                    "generatedAt": snapshot.generated_at,
                    "posts": snapshot.posts,
                    "stats": snapshot.stats,
                }
            )
            return
        if parsed.path.startswith("/api/posts/"):
            post_id = parsed.path.rsplit("/", 1)[-1]
            snapshot = self.server.archive_index.snapshot()  # type: ignore[attr-defined]
            detail = snapshot.details_by_id.get(post_id)
            if detail is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown post id")
                return
            self._send_json(detail)
            return
        if parsed.path.startswith("/media/"):
            filename = unquote(parsed.path.removeprefix("/media/"))
            self._serve_media(filename)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve_asset(self, filename: str, content_type: str) -> None:
        asset = files("xsync.viewer_assets").joinpath(filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(asset)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(asset)

    def _serve_media(self, filename: str) -> None:
        if not filename or "/" in filename or filename.startswith("."):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid filename")
            return
        media_dir = self.server.archive_index.paths["media"]  # type: ignore[attr-defined]
        target = (media_dir / filename).resolve()
        if target.parent != media_dir.resolve() or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Media not found")
            return
        content = target.read_bytes()
        content_type = _guess_media_type(target)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def run_viewer(
    archive_root: Path | None,
    *,
    host: str,
    port: int,
    open_browser: bool,
) -> None:
    resolved_archive_root = resolve_archive_root(archive_root)
    archive_index = ArchiveIndex(resolved_archive_root)
    server = _bind_server(host, port, archive_index)
    url = f"http://{_display_host(host)}:{server.server_port}"
    typer.echo(f"Serving archive from {resolved_archive_root}")
    typer.echo(f"Viewer available at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("Stopping viewer.")
    finally:
        server.server_close()


def resolve_archive_root(archive_root: Path | None) -> Path:
    if archive_root is not None:
        resolved = archive_root.resolve()
        _validate_archive_root(resolved)
        return resolved

    candidates = []
    env_path = os.environ.get(ARCHIVE_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path))
    cwd = Path.cwd()
    candidates.extend(
        (
            cwd,
            cwd / "x-archive",
            cwd.parent / "x-archive",
            Path(__file__).resolve().parents[3] / "x-archive",
        )
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if _looks_like_archive_root(resolved):
            return resolved
    raise FileNotFoundError(
        "Could not locate an archive repo. Pass --archive-root or set "
        f"{ARCHIVE_ENV_VAR}."
    )


def _validate_archive_root(path: Path) -> None:
    if not _looks_like_archive_root(path):
        raise FileNotFoundError(
            f"{path} does not look like an xsync archive root "
            "(missing x-posts/x-bookmarks/x-threads/x-media)."
        )


def _looks_like_archive_root(path: Path) -> bool:
    required = ("x-posts", "x-bookmarks", "x-threads", "x-media")
    return all((path / name).is_dir() for name in required)


def _bind_server(host: str, port: int, archive_index: ArchiveIndex) -> ViewerServer:
    if port == 0:
        return ViewerServer(host, 0, archive_index)
    last_error: OSError | None = None
    for offset in range(PORT_SCAN_LIMIT):
        candidate = port + offset
        try:
            return ViewerServer(host, candidate, archive_index)
        except OSError as exc:
            last_error = exc
            if exc.errno != errno.EADDRINUSE:
                raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to bind viewer server")


def _display_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    return host


def _conversation_sizes(
    markdown_posts: dict[str, dict[str, Any]],
    threads: dict[str, dict[str, Any]],
) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for post in markdown_posts.values():
        conversation_id = post["conversation_id"]
        sizes[conversation_id] = sizes.get(conversation_id, 0) + 1
    for conversation_id, thread in threads.items():
        sizes[conversation_id] = max(sizes.get(conversation_id, 0), len(thread.get("posts", [])))
    return sizes


def _load_bookmarks(bookmarks_dir: Path) -> dict[str, dict[str, str]]:
    bookmarks: dict[str, dict[str, str]] = {}
    for path in sorted(bookmarks_dir.glob("*.md")):
        frontmatter, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        post_id = frontmatter.get("post_id")
        if post_id:
            bookmarks[str(post_id)] = frontmatter
    return bookmarks


def _load_threads(threads_dir: Path) -> dict[str, dict[str, Any]]:
    threads: dict[str, dict[str, Any]] = {}
    for path in sorted(threads_dir.glob("*.json")):
        thread = json.loads(path.read_text(encoding="utf-8"))
        threads[str(thread["conversation_id"])] = thread
    return threads


def _load_markdown_posts(posts_dir: Path) -> dict[str, dict[str, Any]]:
    posts: dict[str, dict[str, Any]] = {}
    for path in sorted(posts_dir.glob("*.md")):
        frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        body_text, references, media, metrics = _parse_markdown_post_sections(body)
        post_id = str(frontmatter["id"])
        posts[post_id] = {
            "id": post_id,
            "conversation_id": str(frontmatter.get("conversation_id", post_id)),
            "url": str(frontmatter.get("url", "")),
            "created_at": str(frontmatter.get("created_at", "")),
            "author_username": str(frontmatter.get("author_username", "")),
            "author_name": str(frontmatter.get("author_name", "")),
            "text": body_text,
            "references": references,
            "media": media,
            "metrics": metrics,
            "path": str(path),
        }
    return posts


def _build_post_card(
    *,
    markdown_post: dict[str, Any],
    thread_post: dict[str, Any] | None,
    thread: dict[str, Any] | None,
    bookmarks: dict[str, dict[str, str]],
    media_dir: Path,
    conversation_sizes: dict[str, int],
) -> dict[str, Any]:
    author = _build_author(markdown_post, thread_post)
    media = _build_media_list(markdown_post, thread_post, media_dir)
    metrics = _build_metrics(markdown_post, thread_post)
    links = _build_links(thread_post)
    references = _build_references(markdown_post, thread_post, {})
    reply_to_post_id = _reply_to_post_id(thread_post, markdown_post)
    fallback_thread_size = len(thread.get("posts", [])) if thread else 1
    thread_size = conversation_sizes.get(markdown_post["conversation_id"], fallback_thread_size)
    return {
        "id": markdown_post["id"],
        "conversationId": markdown_post["conversation_id"],
        "url": markdown_post["url"],
        "createdAt": markdown_post["created_at"],
        "author": author,
        "text": _post_text(markdown_post, thread_post),
        "metrics": metrics,
        "media": media,
        "links": links,
        "references": references,
        "bookmarked": markdown_post["id"] in bookmarks,
        "bookmark": bookmarks.get(markdown_post["id"]),
        "threadSize": thread_size,
        "hasThread": thread_size > 1,
        "replyToPostId": reply_to_post_id,
        "archivedPath": markdown_post["path"],
    }


def _build_post_detail(
    *,
    post_id: str,
    markdown_post: dict[str, Any],
    thread: dict[str, Any] | None,
    bookmarks: dict[str, dict[str, str]],
    media_dir: Path,
    post_cards: dict[str, dict[str, Any]],
    conversation_sizes: dict[str, int],
) -> dict[str, Any]:
    thread_post = _find_thread_post(thread, post_id)
    card = _build_post_card(
        markdown_post=markdown_post,
        thread_post=thread_post,
        thread=thread,
        bookmarks=bookmarks,
        media_dir=media_dir,
        conversation_sizes=conversation_sizes,
    )
    card["references"] = _build_references(markdown_post, thread_post, post_cards)
    thread_posts = []
    if thread:
        ordered = sorted(
            thread.get("posts", []),
            key=lambda item: (item.get("post", {}).get("created_at", ""), item.get("id", "")),
        )
        for record in ordered:
            thread_posts.append(
                {
                    "id": str(record.get("id", "")),
                    "conversationId": str(record.get("conversation_id", "")),
                    "url": str(record.get("url", "")),
                    "createdAt": str(record.get("post", {}).get("created_at", "")),
                    "author": _build_author(markdown_post=None, thread_post=record),
                    "text": _post_text(None, record),
                    "metrics": _build_metrics(None, record),
                    "media": _build_media_list(None, record, media_dir),
                    "links": _build_links(record),
                    "replyToPostId": _reply_to_post_id(record, None),
                    "isSelected": str(record.get("id")) == post_id,
                }
            )
    else:
        thread_posts.append({**card, "isSelected": True})
    return {
        "archiveRoot": str(media_dir.parent),
        "generatedAt": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "post": card,
        "thread": {
            "conversationId": markdown_post["conversation_id"],
            "rootPostId": (
                str(thread.get("root_post_id", markdown_post["conversation_id"]))
                if thread
                else markdown_post["id"]
            ),
            "missingPostIds": list(thread.get("missing_post_ids", [])) if thread else [],
            "posts": thread_posts,
            "size": len(thread_posts),
        },
    }


def _build_author(
    markdown_post: dict[str, Any] | None,
    thread_post: dict[str, Any] | None,
) -> dict[str, Any]:
    author = thread_post.get("author", {}) if thread_post else {}
    username = str(author.get("username") or (markdown_post or {}).get("author_username", ""))
    return {
        "name": str(author.get("name") or (markdown_post or {}).get("author_name", "")),
        "username": username,
        "verified": bool(author.get("verified", False)),
        "avatarUrl": str(author.get("profile_image_url", "")),
        "profileUrl": f"https://x.com/{username}" if username else "",
    }


def _build_metrics(
    markdown_post: dict[str, Any] | None,
    thread_post: dict[str, Any] | None,
) -> dict[str, int]:
    if thread_post:
        metrics = thread_post.get("post", {}).get("public_metrics", {})
    else:
        metrics = (markdown_post or {}).get("metrics", {})
    return {
        "bookmarkCount": int(metrics.get("bookmark_count", 0)),
        "impressionCount": int(metrics.get("impression_count", 0)),
        "likeCount": int(metrics.get("like_count", 0)),
        "quoteCount": int(metrics.get("quote_count", 0)),
        "replyCount": int(metrics.get("reply_count", 0)),
        "retweetCount": int(metrics.get("retweet_count", 0)),
    }


def _build_links(thread_post: dict[str, Any] | None) -> list[dict[str, str]]:
    if not thread_post:
        return []
    links = []
    for item in thread_post.get("post", {}).get("entities", {}).get("urls", []):
        links.append(
            {
                "url": str(
                    item.get("unwound_url") or item.get("expanded_url") or item.get("url") or ""
                ),
                "displayUrl": str(item.get("display_url") or item.get("url") or ""),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    return [item for item in links if item["url"]]


def _build_references(
    markdown_post: dict[str, Any] | None,
    thread_post: dict[str, Any] | None,
    post_cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    references = []
    if thread_post:
        for item in thread_post.get("post", {}).get("referenced_tweets", []):
            target_id = str(item.get("id", ""))
            references.append(
                {
                    "type": str(item.get("type", "unknown")),
                    "targetId": target_id,
                    "isArchived": target_id in post_cards,
                    "target": post_cards.get(target_id),
                }
            )
    elif markdown_post:
        for item in markdown_post.get("references", []):
            target_id = str(item.get("target_id", ""))
            references.append(
                {
                    "type": str(item.get("type", "unknown")),
                    "targetId": target_id,
                    "isArchived": target_id in post_cards,
                    "target": post_cards.get(target_id),
                }
            )
    return references


def _build_media_list(
    markdown_post: dict[str, Any] | None,
    thread_post: dict[str, Any] | None,
    media_dir: Path,
) -> list[dict[str, Any]]:
    items = thread_post.get("media", []) if thread_post else (markdown_post or {}).get("media", [])
    media = []
    for item in items:
        media_type = str(item.get("type", ""))
        media_key = str(item.get("media_key", ""))
        preview_name = _find_media_filename(media_dir, media_key, media_type)
        variants = item.get("variants", []) if isinstance(item, dict) else []
        video_variants = [
            variant
            for variant in variants
            if variant.get("content_type") == "video/mp4" and variant.get("url")
        ]
        best_video = max(
            video_variants,
            key=lambda variant: variant.get("bit_rate", 0),
            default=None,
        )
        media.append(
            {
                "mediaKey": media_key,
                "type": media_type,
                "altText": str(item.get("alt_text", "")),
                "width": int(item.get("width", 0) or 0),
                "height": int(item.get("height", 0) or 0),
                "localUrl": f"/media/{preview_name}" if preview_name else "",
                "remoteUrl": str(item.get("url") or item.get("preview_image_url") or ""),
                "videoUrl": str(best_video.get("url", "")) if best_video else "",
            }
        )
    return media


def _find_thread_post(thread: dict[str, Any] | None, post_id: str) -> dict[str, Any] | None:
    if not thread:
        return None
    for item in thread.get("posts", []):
        if str(item.get("id")) == post_id:
            return item
    return None


def _post_text(markdown_post: dict[str, Any] | None, thread_post: dict[str, Any] | None) -> str:
    if thread_post:
        return str(thread_post.get("post", {}).get("text", "")).strip()
    return str((markdown_post or {}).get("text", "")).strip()


def _reply_to_post_id(
    thread_post: dict[str, Any] | None,
    markdown_post: dict[str, Any] | None,
) -> str:
    if thread_post:
        for item in thread_post.get("post", {}).get("referenced_tweets", []):
            if item.get("type") == "replied_to":
                return str(item.get("id", ""))
        return ""
    if markdown_post:
        for item in markdown_post.get("references", []):
            if item.get("type") == "replied_to":
                return str(item.get("target_id", ""))
    return ""


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---\n"):
        return {}, content
    _, _, remainder = content.partition("---\n")
    frontmatter_text, separator, body = remainder.partition("\n---\n")
    if not separator:
        return {}, content
    frontmatter: dict[str, str] = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body.lstrip()


def _parse_markdown_post_sections(
    body: str,
) -> tuple[str, list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    text, references_chunk = _split_section(body, "## References")
    references_raw, media_chunk = _split_section(references_chunk, "## Media")
    media_raw, metrics_chunk = _split_section(media_chunk, "## Public Metrics")
    return (
        text.strip(),
        _parse_reference_lines(references_raw),
        _parse_markdown_media_lines(media_raw),
        _parse_metrics_block(metrics_chunk),
    )


def _split_section(content: str, heading: str) -> tuple[str, str]:
    marker = f"\n{heading}\n"
    if marker not in content:
        return content, ""
    before, after = content.split(marker, 1)
    return before, after


def _parse_reference_lines(chunk: str) -> list[dict[str, str]]:
    references = []
    for line in chunk.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped == "- none":
            continue
        match = _REFERENCE_RE.match(stripped)
        if match:
            references.append(
                {
                    "type": match.group("kind"),
                    "target_id": match.group("target_id"),
                }
            )
    return references


def _parse_markdown_media_lines(chunk: str) -> list[dict[str, str]]:
    media = []
    for line in chunk.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped == "- none":
            continue
        match = _MARKDOWN_MEDIA_RE.match(stripped)
        if not match:
            continue
        media.append(
            {
                "media_key": match.group("media_key"),
                "type": match.group("media_type"),
                "alt_text": _decode_alt_text(match.group("alt")),
            }
        )
    return media


def _parse_metrics_block(chunk: str) -> dict[str, Any]:
    if "```json" not in chunk:
        return {}
    _, _, remainder = chunk.partition("```json")
    payload, _, _ = remainder.partition("```")
    try:
        return json.loads(payload.strip())
    except json.JSONDecodeError:
        return {}


def _decode_alt_text(raw: str) -> str:
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        value = raw.strip("'\"")
    if isinstance(value, str) and "\\u" in value:
        return value.encode("utf-8").decode("unicode_escape")
    return str(value)


def _find_media_filename(media_dir: Path, media_key: str, media_type: str) -> str:
    candidates = []
    if media_type in {"video", "animated_gif"}:
        candidates.extend(sorted(media_dir.glob(f"{media_key}-preview.*")))
    candidates.extend(sorted(media_dir.glob(f"{media_key}.*")))
    return candidates[0].name if candidates else ""


def _guess_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _directory_signature(path: Path) -> tuple[int, int]:
    count = 0
    latest_mtime = 0
    for entry in path.glob("*"):
        if not entry.is_file():
            continue
        stat = entry.stat()
        count += 1
        latest_mtime = max(latest_mtime, stat.st_mtime_ns)
    return count, latest_mtime
