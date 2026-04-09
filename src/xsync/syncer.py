from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import islice
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
    def __init__(self, api: XApi, store: StateStore, writer: ArchiveWriter) -> None:
        self.api = api
        self.store = store
        self.writer = writer

    def sync_posts(self, username: str) -> SyncResult:
        observed_at = utc_now()
        self.api.usage.clear()
        run_id = self.store.record_run_start("posts")
        counts = {"posts_upserted": 0}
        try:
            since_id = self.store.get_sync_state("authored_since_id")
            max_seen_id = since_id
            changed_post_ids: set[str] = set()
            for page in self.api.search_all(query=f"from:{username}", since_id=since_id):
                includes = page.get("includes", {})
                for post in page.get("data", []):
                    record = build_post_record(post, includes)
                    self.store.upsert_post(record, "authored", observed_at)
                    self.writer.write_post(record)
                    changed_post_ids.add(record["id"])
                    counts["posts_upserted"] += 1
                    max_seen_id = _max_snowflake(max_seen_id, record["id"])
            if max_seen_id:
                self.store.set_sync_state("authored_since_id", max_seen_id)

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
            bookmark_records: list[dict[str, Any]] = []
            thread_seeds: dict[str, list[dict[str, Any]]] = defaultdict(list)
            seen_bookmark_ids: set[str] = set()
            for page in self.api.get_bookmarks(user_id):
                includes = page.get("includes", {})
                for post in page.get("data", []):
                    record = build_post_record(post, includes)
                    bookmark_records.append(record)
                    thread_seeds[record["conversation_id"]].append(record)
                    seen_bookmark_ids.add(record["id"])
                    self.store.upsert_post(record, "bookmark", observed_at)
                    self.writer.write_post(record)
                    counts["bookmarks_seen"] += 1
                    counts["posts_upserted"] += 1

            changed_post_ids: set[str] = set(seen_bookmark_ids)
            changed_thread_ids: set[str] = set()
            for conversation_id, seeds in thread_seeds.items():
                thread_records, missing_ids = self._hydrate_thread(
                    conversation_id,
                    seeds,
                    observed_at,
                )
                thread_doc = build_thread_document(conversation_id, thread_records, missing_ids)
                self.store.upsert_thread(conversation_id, thread_doc, observed_at)
                self.writer.write_thread(thread_doc)
                changed_thread_ids.add(conversation_id)
                counts["threads_written"] += 1

                for record in thread_records:
                    self.store.upsert_post(record, "thread", observed_at)
                    self.writer.write_post(record)
                    changed_post_ids.add(record["id"])
                    counts["posts_upserted"] += 1

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

            removed = self.store.mark_missing_bookmarks_removed(seen_bookmark_ids, observed_at)
            for post_id in removed:
                bookmark = self.store.get_bookmark(post_id)
                record = self.store.get_post(post_id)
                if bookmark and record:
                    self.writer.write_bookmark(bookmark, record)
            counts["bookmarks_removed"] = len(removed)

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
                        "removed_bookmark_ids": sorted(removed),
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
        posts_result = self.sync_posts(username)
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
        observed_at: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records_by_id = {seed["id"]: seed for seed in seeds}
        missing_lookup_ids: set[str] = set()
        for page in self.api.search_all(query=f"conversation_id:{conversation_id}"):
            includes = page.get("includes", {})
            for post in page.get("data", []):
                record = build_post_record(post, includes)
                records_by_id[record["id"]] = record
                self.store.upsert_post(record, "thread", observed_at)
        for record in records_by_id.values():
            for ref in record["post"].get("referenced_tweets", []):
                ref_id = str(ref.get("id"))
                if ref_id and ref_id not in records_by_id:
                    missing_lookup_ids.add(ref_id)

        missing_not_found: list[str] = []
        for chunk in _chunked(sorted(missing_lookup_ids), 100):
            lookup = self.api.get_posts_by_ids(chunk)
            includes = lookup.get("includes", {})
            found_ids: set[str] = set()
            for post in lookup.get("data", []):
                record = build_post_record(post, includes)
                records_by_id[record["id"]] = record
                found_ids.add(record["id"])
            for post_id in chunk:
                if post_id not in found_ids:
                    missing_not_found.append(post_id)

        records = sorted(
            records_by_id.values(),
            key=lambda item: (item["post"].get("created_at", ""), item["id"]),
        )
        return records, missing_not_found


def _chunked(values: list[str], size: int) -> list[list[str]]:
    iterator = iter(values)
    chunks: list[list[str]] = []
    while chunk := list(islice(iterator, size)):
        chunks.append(chunk)
    return chunks


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
