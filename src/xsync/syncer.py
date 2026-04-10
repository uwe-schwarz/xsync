from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from xsync.exporter import ArchiveWriter, build_post_record, build_thread_document
from xsync.git_sync import stage_commit_and_push
from xsync.store import StateStore, utc_now
from xsync.x_api import XApi


@dataclass
class SyncResult:
    scope: str
    timestamp: str
    counts: dict[str, int] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    committed: bool = False
    manifest_path: str | None = None


class SyncService:
    def __init__(
        self,
        api: XApi,
        store: StateStore,
        writer: ArchiveWriter,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.api = api
        self.store = store
        self.writer = writer
        self.progress = progress or (lambda _: None)

    def sync_posts(self, user_id: str, username: str) -> SyncResult:
        observed_at = utc_now()
        self.api.usage.clear()
        run_id = self.store.record_run_start("posts")
        counts = {"posts_upserted": 0}
        try:
            self.progress(f"Syncing original posts for @{username}")
            backfill_complete = self.store.get_sync_state("authored_backfill_complete") == "1"
            since_id = self.store.get_sync_state("authored_since_id")
            max_seen_id = since_id
            changed_post_ids: set[str] = set()
            pages_seen = 0
            if backfill_complete:
                pages = self.api.get_user_posts(user_id, since_id=since_id)
            else:
                pages = self.api.search_all(query=_authored_posts_query(username))

            for page in pages:
                pages_seen += 1
                includes = page.get("includes", {})
                for post in page.get("data", []):
                    record = build_post_record(post, includes)
                    if self.store.has_post(record["id"]):
                        continue
                    self.store.upsert_post(record, "authored", observed_at)
                    self.writer.write_post(record)
                    changed_post_ids.add(record["id"])
                    counts["posts_upserted"] += 1
                    max_seen_id = _max_snowflake(max_seen_id, record["id"])
                self.progress(
                    f"Posts progress: {counts['posts_upserted']} originals fetched across "
                    f"{pages_seen} page(s)"
                )
            self.store.set_sync_state("authored_backfill_complete", "1")
            if max_seen_id:
                self.store.set_sync_state("authored_since_id", max_seen_id)
            self.progress(f"Posts complete: {counts['posts_upserted']} originals")

            result = SyncResult(
                scope="posts",
                timestamp=observed_at,
                counts=counts,
                usage=dict(self.api.usage),
            )
            result.manifest_path = str(
                self.writer.write_run_manifest(
                    observed_at,
                    {
                        "scope": "posts",
                        "completed_at": observed_at,
                        "counts": counts,
                        "usage": dict(self.api.usage),
                        "changed_post_ids": sorted(changed_post_ids),
                    },
                )
            )
            self.store.record_run_finish(
                run_id,
                status="success",
                counts=counts,
                usage=dict(self.api.usage),
            )
            return result
        except Exception as exc:
            self.store.record_run_finish(
                run_id,
                status="error",
                counts=counts,
                usage=dict(self.api.usage),
                error_text=str(exc),
            )
            raise

    def sync_bookmarks(self, user_id: str) -> SyncResult:
        observed_at = utc_now()
        self.api.usage.clear()
        run_id = self.store.record_run_start("bookmarks")
        counts = {
            "bookmarks_seen": 0,
            "threads_written": 0,
            "posts_upserted": 0,
            "bookmarks_removed": 0,
        }
        try:
            self.progress("Syncing bookmarks")
            bookmark_records: list[dict[str, Any]] = []
            thread_seeds: dict[str, list[dict[str, Any]]] = defaultdict(list)
            bookmark_pages = 0
            known_bookmark_seen = False
            for page in self.api.get_bookmarks(user_id):
                bookmark_pages += 1
                includes = page.get("includes", {})
                for post in page.get("data", []):
                    record = build_post_record(post, includes)
                    if self.store.has_bookmark(record["id"]):
                        known_bookmark_seen = True
                        break
                    bookmark_records.append(record)
                    thread_seeds[record["conversation_id"]].append(record)
                    self.store.upsert_post(record, "bookmark", observed_at)
                    self.writer.write_post(record)
                    counts["bookmarks_seen"] += 1
                    counts["posts_upserted"] += 1
                self.progress(
                    f"Bookmarks progress: {counts['bookmarks_seen']} bookmarks across "
                    f"{bookmark_pages} page(s)"
                )
                if known_bookmark_seen:
                    break

            changed_post_ids: set[str] = {record["id"] for record in bookmark_records}
            changed_thread_ids: set[str] = set()
            total_threads = len(thread_seeds)
            for index, (conversation_id, seeds) in enumerate(thread_seeds.items(), start=1):
                author_username = _record_author_username(seeds[0]) or "unknown"
                self.progress(
                    f"Hydrating bookmark thread {index}/{total_threads}: "
                    f"{conversation_id} from @{author_username}"
                )
                thread_records, missing_ids = self._hydrate_thread(
                    conversation_id,
                    seeds,
                )
                thread_doc = build_thread_document(conversation_id, thread_records, missing_ids)
                self.store.upsert_thread(conversation_id, thread_doc, observed_at)
                self.writer.write_thread(thread_doc)
                changed_thread_ids.add(conversation_id)
                counts["threads_written"] += 1

                for record in thread_records:
                    if self.store.has_post(record["id"]):
                        continue
                    self.store.upsert_post(record, "thread", observed_at)
                    self.writer.write_post(record)
                    changed_post_ids.add(record["id"])
                    counts["posts_upserted"] += 1
                self.progress(
                    f"Thread progress: {counts['threads_written']}/{total_threads} thread(s), "
                    f"{counts['posts_upserted']} posts materialized"
                )

            for record in bookmark_records:
                bookmark_payload = {
                    "post_id": record["id"],
                    "conversation_id": record["conversation_id"],
                    "url": record["url"],
                }
                self.store.upsert_bookmark(
                    record["id"],
                    record["conversation_id"],
                    bookmark_payload,
                    observed_at,
                )
                bookmark = self.store.get_bookmark(record["id"])
                if bookmark:
                    self.writer.write_bookmark(bookmark, record)

            self.progress(
                f"Bookmarks complete: {counts['bookmarks_seen']} bookmarks, "
                f"{counts['threads_written']} thread docs"
            )

            result = SyncResult(
                scope="bookmarks",
                timestamp=observed_at,
                counts=counts,
                usage=dict(self.api.usage),
            )
            result.manifest_path = str(
                self.writer.write_run_manifest(
                    observed_at,
                    {
                        "scope": "bookmarks",
                        "completed_at": observed_at,
                        "counts": counts,
                        "usage": dict(self.api.usage),
                        "changed_post_ids": sorted(changed_post_ids),
                        "changed_thread_ids": sorted(changed_thread_ids),
                        "removed_bookmark_ids": [],
                    },
                )
            )
            self.store.record_run_finish(
                run_id,
                status="success",
                counts=counts,
                usage=dict(self.api.usage),
            )
            return result
        except Exception as exc:
            self.store.record_run_finish(
                run_id,
                status="error",
                counts=counts,
                usage=dict(self.api.usage),
                error_text=str(exc),
            )
            raise

    def sync_all(
        self,
        username: str,
        user_id: str,
        *,
        repo_root,
        git_remote: str,
        git_branch: str | None,
        auto_push: bool,
    ) -> SyncResult:
        start = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        posts_result = self.sync_posts(user_id, username)
        bookmarks_result = self.sync_bookmarks(user_id)
        combined_counts = _merge_counts(posts_result.counts, bookmarks_result.counts)
        combined_usage = _merge_counts(posts_result.usage, bookmarks_result.usage)
        combined = SyncResult(
            scope="all",
            timestamp=start,
            counts=combined_counts,
            usage=combined_usage,
        )
        manifest = {
            "scope": "all",
            "completed_at": start,
            "counts": combined_counts,
            "usage": combined_usage,
            "children": [posts_result.manifest_path, bookmarks_result.manifest_path],
        }
        combined.manifest_path = str(self.writer.write_run_manifest(start, manifest))
        if auto_push:
            combined.committed = stage_commit_and_push(
                repo_root=repo_root,
                remote=git_remote,
                branch=git_branch,
                message=f"chore(sync): x archive {start}",
            )
        return combined

    def _hydrate_thread(
        self,
        conversation_id: str,
        seeds: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records_by_id = _thread_records_by_id(self.store.get_thread(conversation_id))
        pending_parent_ids = []
        pending_parent_id_set: set[str] = set()
        for seed in seeds:
            records_by_id[seed["id"]] = seed
            pending_parent_id_set.add(seed["id"])
            pending_parent_ids.append(seed["id"])
        hydrated_parent_ids: set[str] = set()
        while pending_parent_ids:
            parent_id = pending_parent_ids.pop()
            if parent_id in hydrated_parent_ids:
                continue
            hydrated_parent_ids.add(parent_id)
            pending_parent_id_set.discard(parent_id)

            parent = records_by_id.get(parent_id)
            if parent is None:
                continue
            author_username = _record_author_username(parent)
            if author_username is None:
                continue

            state_key = _thread_parent_state_key(conversation_id, parent_id)
            since_id = self.store.get_sync_state(state_key)
            max_seen_id = since_id or parent_id
            for page in self.api.search_all(
                query=_bookmark_thread_query(conversation_id, parent_id, author_username),
                since_id=since_id,
            ):
                includes = page.get("includes", {})
                for post in page.get("data", []):
                    record = build_post_record(post, includes)
                    record_id = record["id"]
                    if (
                        record_id not in records_by_id
                        and record_id not in hydrated_parent_ids
                        and record_id not in pending_parent_id_set
                    ):
                        pending_parent_id_set.add(record_id)
                        pending_parent_ids.append(record_id)
                    records_by_id[record_id] = record
                    max_seen_id = _max_snowflake(max_seen_id, record_id)
            if max_seen_id:
                self.store.set_sync_state(state_key, max_seen_id)

        records = sorted(
            records_by_id.values(),
            key=lambda item: (item["post"].get("created_at", ""), item["id"]),
        )
        return records, []


def _max_snowflake(lhs: str | None, rhs: str | None) -> str | None:
    if lhs is None:
        return rhs
    if rhs is None:
        return lhs
    return lhs if int(lhs) >= int(rhs) else rhs


def _merge_counts(*mappings: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for mapping in mappings:
        for key, value in mapping.items():
            merged[key] = merged.get(key, 0) + value
    return merged


def _authored_posts_query(username: str) -> str:
    return f"from:{username} -is:reply -is:retweet"


def _bookmark_thread_query(conversation_id: str, parent_id: str, author_username: str) -> str:
    return (
        f"conversation_id:{conversation_id} "
        f"in_reply_to_tweet_id:{parent_id} "
        f"from:{author_username}"
    )


def _thread_parent_state_key(conversation_id: str, parent_id: str) -> str:
    return f"thread_parent_since_id:{conversation_id}:{parent_id}"


def _thread_records_by_id(thread: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not thread:
        return {}
    return {
        str(record["id"]): record
        for record in thread.get("posts", [])
        if isinstance(record, dict) and record.get("id")
    }


def _record_author_username(record: dict[str, Any]) -> str | None:
    author = record.get("author") or {}
    username = author.get("username")
    if username:
        return str(username)
    return None
