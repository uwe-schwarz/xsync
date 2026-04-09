from __future__ import annotations

import subprocess
from pathlib import Path

ARCHIVE_PATHS = ["x-posts", "x-bookmarks", "x-threads", "x-media", "x-sync-runs"]


def stage_commit_and_push(repo_root: Path, remote: str, branch: str | None, message: str) -> bool:
    _run(["git", "add", "--", *ARCHIVE_PATHS], cwd=repo_root)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", *ARCHIVE_PATHS],
        cwd=repo_root,
        check=False,
    )
    if staged.returncode == 0:
        return False
    _run(["git", "commit", "-m", message], cwd=repo_root)
    push_cmd = ["git", "push", remote]
    if branch:
        push_cmd.append(branch)
    _run(push_cmd, cwd=repo_root)
    return True


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)
