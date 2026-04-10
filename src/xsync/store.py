from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    author_id TEXT,
                    created_at TEXT,
                    post_json TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bookmarks (
                    post_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    first_seen_bookmarked_at TEXT NOT NULL,
                    last_seen_bookmarked_at TEXT NOT NULL,
                    removed_at TEXT,
                    bookmark_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    thread_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    counts_json TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    error_text TEXT
                );
                """
            )

    def record_run_start(self, scope: str) -> int:
        started_at = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_runs(scope, started_at, status, counts_json, usage_json)
                VALUES (?, ?, 'running', '{}', '{}')
                """,
                (scope, started_at),
            )
            return int(cursor.lastrowid)

    def record_run_finish(
        self,
        run_id: int,
        *,
        status: str,
        counts: dict[str, Any],
        usage: dict[str, Any],
        error_text: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?, status = ?, counts_json = ?, usage_json = ?, error_text = ?
                WHERE id = ?
                """,
                (utc_now(), status, json_dumps(counts), json_dumps(usage), error_text, run_id),
            )

    def get_sync_state(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
            return None if row is None else str(row["value"])

    def set_sync_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def upsert_post(self, record: dict[str, Any], source: str, observed_at: str) -> None:
        post_id = str(record["id"])
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT sources_json, first_seen_at FROM posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if existing:
                sources = sorted(set(json.loads(existing["sources_json"])) | {source})
                first_seen = str(existing["first_seen_at"])
            else:
                sources = [source]
                first_seen = observed_at
            conn.execute(
                """
                INSERT INTO posts(
                    id,
                    conversation_id,
                    author_id,
                    created_at,
                    post_json,
                    sources_json,
                    first_seen_at,
                    last_seen_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    author_id = excluded.author_id,
                    created_at = excluded.created_at,
                    post_json = excluded.post_json,
                    sources_json = excluded.sources_json,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    post_id,
                    str(record.get("conversation_id") or post_id),
                    record.get("author_id"),
                    record.get("created_at"),
                    json_dumps(record),
                    json_dumps(sources),
                    first_seen,
                    observed_at,
                    observed_at,
                ),
            )

    def get_post(self, post_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT post_json FROM posts WHERE id = ?", (post_id,)).fetchone()
            return None if row is None else json.loads(row["post_json"])

    def has_post(self, post_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
            return row is not None

    def upsert_thread(self, conversation_id: str, thread: dict[str, Any], observed_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO threads(id, thread_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    thread_json = excluded.thread_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, json_dumps(thread), observed_at),
            )

    def get_thread(self, conversation_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT thread_json FROM threads WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            return None if row is None else json.loads(row["thread_json"])

    def upsert_bookmark(
        self,
        post_id: str,
        thread_id: str,
        bookmark: dict[str, Any],
        observed_at: str,
    ) -> None:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT first_seen_bookmarked_at FROM bookmarks WHERE post_id = ?",
                (post_id,),
            ).fetchone()
            first_seen = (
                observed_at if existing is None else str(existing["first_seen_bookmarked_at"])
            )
            conn.execute(
                """
                INSERT INTO bookmarks(
                    post_id,
                    thread_id,
                    first_seen_bookmarked_at,
                    last_seen_bookmarked_at,
                    removed_at,
                    bookmark_json
                )
                VALUES(?, ?, ?, ?, NULL, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    last_seen_bookmarked_at = excluded.last_seen_bookmarked_at,
                    removed_at = NULL,
                    bookmark_json = excluded.bookmark_json
                """,
                (post_id, thread_id, first_seen, observed_at, json_dumps(bookmark)),
            )

    def mark_missing_bookmarks_removed(
        self,
        seen_post_ids: set[str],
        observed_at: str,
    ) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT post_id FROM bookmarks WHERE removed_at IS NULL",
            ).fetchall()
            removed: list[str] = []
            for row in rows:
                post_id = str(row["post_id"])
                if post_id in seen_post_ids:
                    continue
                removed.append(post_id)
                conn.execute(
                    "UPDATE bookmarks SET removed_at = ? WHERE post_id = ?",
                    (observed_at, post_id),
                )
            return removed

    def get_bookmark(self, post_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    post_id,
                    thread_id,
                    first_seen_bookmarked_at,
                    last_seen_bookmarked_at,
                    removed_at,
                    bookmark_json
                FROM bookmarks WHERE post_id = ?
                """,
                (post_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "post_id": str(row["post_id"]),
                "thread_id": row["thread_id"],
                "first_seen_bookmarked_at": row["first_seen_bookmarked_at"],
                "last_seen_bookmarked_at": row["last_seen_bookmarked_at"],
                "removed_at": row["removed_at"],
                "bookmark": json.loads(row["bookmark_json"]),
            }

    def has_bookmark(self, post_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM bookmarks WHERE post_id = ?", (post_id,)).fetchone()
            return row is not None
