from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SCOPES = ("tweet.read", "users.read", "bookmark.read", "offline.access")


@dataclass(frozen=True)
class RepoPaths:
    root: Path
    state_dir: Path
    config_file: Path
    token_file: Path
    state_db: Path
    posts_dir: Path
    bookmarks_dir: Path
    threads_dir: Path
    media_dir: Path
    runs_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> RepoPaths:
        state_dir = root / ".xsync"
        return cls(
            root=root,
            state_dir=state_dir,
            config_file=state_dir / "config.toml",
            token_file=state_dir / "token.json",
            state_db=state_dir / "state.db",
            posts_dir=root / "x-posts",
            bookmarks_dir=root / "x-bookmarks",
            threads_dir=root / "x-threads",
            media_dir=root / "x-media",
            runs_dir=root / "x-sync-runs",
        )

    def ensure(self) -> None:
        for path in (
            self.state_dir,
            self.posts_dir,
            self.bookmarks_dir,
            self.threads_dir,
            self.media_dir,
            self.runs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class XConfig:
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    client_secret: str | None = None
    bearer_token: str | None = None


@dataclass(frozen=True)
class GitConfig:
    remote: str = "origin"
    branch: str | None = None
    auto_push: bool = True


@dataclass(frozen=True)
class AppConfig:
    x: XConfig
    git: GitConfig

    @classmethod
    def load(cls, paths: RepoPaths) -> AppConfig:
        raw = _load_toml(paths.config_file)
        x_cfg = raw.get("x", {})
        git_cfg = raw.get("git", {})

        client_id = os.environ.get("XSYNC_CLIENT_ID", x_cfg.get("client_id", "")).strip()
        redirect_uri = os.environ.get(
            "XSYNC_REDIRECT_URI",
            x_cfg.get("redirect_uri", ""),
        ).strip()
        client_secret = (
            os.environ.get(
                "XSYNC_CLIENT_SECRET",
                x_cfg.get("client_secret", ""),
            ).strip()
            or None
        )
        bearer_token = (
            os.environ.get(
                "XSYNC_BEARER_TOKEN",
                x_cfg.get("bearer_token", ""),
            ).strip()
            or None
        )

        scope_value = os.environ.get("XSYNC_SCOPES")
        if scope_value:
            scopes = tuple(piece.strip() for piece in scope_value.split(",") if piece.strip())
        else:
            config_scopes = x_cfg.get("scopes") or list(DEFAULT_SCOPES)
            scopes = tuple(str(item).strip() for item in config_scopes if str(item).strip())

        remote = (
            os.environ.get(
                "XSYNC_GIT_REMOTE",
                git_cfg.get("remote", "origin"),
            ).strip()
            or "origin"
        )
        branch = os.environ.get("XSYNC_GIT_BRANCH", git_cfg.get("branch", "")).strip() or None
        auto_push_env = os.environ.get("XSYNC_AUTO_PUSH")
        if auto_push_env is None:
            auto_push = bool(git_cfg.get("auto_push", True))
        else:
            auto_push = auto_push_env.strip().lower() in {"1", "true", "yes", "on"}

        if not client_id:
            raise ValueError(f"Missing X client_id in {paths.config_file}")
        if not redirect_uri:
            raise ValueError(f"Missing X redirect_uri in {paths.config_file}")

        return cls(
            x=XConfig(
                client_id=client_id,
                client_secret=client_secret,
                bearer_token=bearer_token,
                redirect_uri=redirect_uri,
                scopes=scopes or DEFAULT_SCOPES,
            ),
            git=GitConfig(remote=remote, branch=branch, auto_push=auto_push),
        )


def write_example_config(paths: RepoPaths, force: bool = False) -> Path:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    if paths.config_file.exists() and not force:
        raise FileExistsError(f"{paths.config_file} already exists")

    content = """[x]
client_id = ""
client_secret = ""
bearer_token = ""
redirect_uri = "http://127.0.0.1:8787/callback"
scopes = ["tweet.read", "users.read", "bookmark.read", "offline.access"]

[git]
remote = "origin"
branch = "main"
auto_push = true
"""
    paths.config_file.write_text(content, encoding="utf-8")
    return paths.config_file


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)
