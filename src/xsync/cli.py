from __future__ import annotations

import json
import webbrowser
from collections.abc import Callable
from pathlib import Path

import typer

from xsync.config import AppConfig, RepoPaths, write_example_config
from xsync.exporter import ArchiveWriter
from xsync.store import StateStore
from xsync.syncer import SyncService
from xsync.token import TokenManager, TokenStore, wait_for_oauth_callback
from xsync.x_api import XApi

app = typer.Typer(help="Archive your personal X posts and bookmarks.")
sync_app = typer.Typer(help="Run sync jobs.")
app.add_typer(sync_app, name="sync")

type Services = tuple[
    RepoPaths,
    AppConfig,
    TokenManager,
    XApi,
    StateStore,
    ArchiveWriter,
    SyncService,
]


def _paths(repo_root: Path | None = None) -> RepoPaths:
    return RepoPaths.from_root((repo_root or Path.cwd()).resolve())


def _services(
    repo_root: Path | None = None,
    *,
    progress: Callable[[str], None] | None = None,
) -> Services:
    paths = _paths(repo_root)
    paths.ensure()
    config = AppConfig.load(paths)
    token_manager = TokenManager(config.x, TokenStore(paths.token_file))
    api = XApi(config.x, token_manager, progress=progress)
    store = StateStore(paths.state_db)
    writer = ArchiveWriter(paths)
    syncer = SyncService(api, store, writer, progress=progress)
    return paths, config, token_manager, api, store, writer, syncer


def _progress(message: str) -> None:
    typer.echo(message, err=True)


@app.command("init-config")
def init_config(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing config file.",
    ),
) -> None:
    paths = _paths()
    path = write_example_config(paths, force=force)
    typer.echo(f"Wrote {path}")


@app.command()
def auth(
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-open-browser",
        help="Open the authorization URL in a browser.",
    ),
    timeout: int = typer.Option(180, min=30, help="Seconds to wait for the OAuth callback."),
) -> None:
    paths, config, token_manager, api, store, writer, syncer = _services()  # noqa: ARG001
    oauth_session = token_manager.create_oauth_session()
    typer.echo("Authorize this app in X:")
    typer.echo(oauth_session.authorization_url)
    if open_browser:
        webbrowser.open(oauth_session.authorization_url)
    callback_url = wait_for_oauth_callback(config.x.redirect_uri, timeout_seconds=timeout)
    token = token_manager.exchange_callback_url(callback_url, oauth_session)
    me = api.get_me()
    account = me["data"]
    typer.echo(
        f"Authenticated as @{account.get('username')} ({account.get('id')}). "
        f"Token expires_at={token.get('expires_at', 'unknown')}"
    )


@app.command()
def whoami() -> None:
    paths, config, token_manager, api, store, writer, syncer = _services()  # noqa: ARG001
    me = api.get_me()
    typer.echo(json.dumps(me, indent=2, ensure_ascii=False, sort_keys=True))


@sync_app.command("posts")
def sync_posts() -> None:
    paths, config, token_manager, api, store, writer, syncer = _services(  # noqa: ARG001
        progress=_progress
    )
    me = api.get_me()["data"]
    result = syncer.sync_posts(str(me["username"]))
    typer.echo(json.dumps(result.__dict__, indent=2, ensure_ascii=False, sort_keys=True))


@sync_app.command("bookmarks")
def sync_bookmarks() -> None:
    paths, config, token_manager, api, store, writer, syncer = _services(  # noqa: ARG001
        progress=_progress
    )
    me = api.get_me()["data"]
    result = syncer.sync_bookmarks(str(me["id"]))
    typer.echo(json.dumps(result.__dict__, indent=2, ensure_ascii=False, sort_keys=True))


@sync_app.command("all")
def sync_all(
    no_push: bool = typer.Option(
        False,
        "--no-push",
        help="Skip git commit/push even if auto_push is enabled.",
    ),
) -> None:
    paths, config, token_manager, api, store, writer, syncer = _services(  # noqa: ARG001
        progress=_progress
    )
    me = api.get_me()["data"]
    result = syncer.sync_all(
        username=str(me["username"]),
        user_id=str(me["id"]),
        repo_root=paths.root,
        git_remote=config.git.remote,
        git_branch=config.git.branch,
        auto_push=config.git.auto_push and not no_push,
    )
    typer.echo(json.dumps(result.__dict__, indent=2, ensure_ascii=False, sort_keys=True))
