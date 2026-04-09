from __future__ import annotations

import subprocess
from pathlib import Path

from xsync.git_sync import stage_commit_and_push


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def test_stage_commit_and_push(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    remote.mkdir()
    repo.mkdir()

    _run(["git", "init", "--bare"], cwd=remote)
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "xsync@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "xsync"], cwd=repo)
    _run(["git", "remote", "add", "origin", str(remote)], cwd=repo)
    _run(["git", "branch", "-M", "main"], cwd=repo)

    for directory in ["x-posts", "x-bookmarks", "x-threads", "x-media", "x-sync-runs"]:
        path = repo / directory
        path.mkdir()
        (path / "sample.txt").write_text("content", encoding="utf-8")

    changed = stage_commit_and_push(repo, "origin", "main", "test commit")
    assert changed is True

    log = subprocess.run(
        ["git", "log", "--oneline", "--max-count", "1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "test commit" in log.stdout
