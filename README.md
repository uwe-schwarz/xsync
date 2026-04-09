# xsync

`xsync` is a Python 3.14+ CLI that archives your X posts and bookmarks into Git-friendly files, keeps sync state in SQLite, hydrates bookmarked threads, downloads image attachments locally, and can auto-commit/push to a private GitHub repo after successful runs.

## What you need from X

Sign up at [console.x.com](https://console.x.com), then:

1. Create a developer account.
2. Create a Project and App.
3. Enable OAuth 2.0 Authorization Code with PKCE.
4. Register a callback URL such as `http://127.0.0.1:8787/callback`.
5. Enable scopes `tweet.read`, `users.read`, `bookmark.read`, `offline.access`.
6. Make sure API credits are enabled for the app.

You need to provide:

- `client_id`
- `redirect_uri`
- optionally `client_secret` if you configured the app as a confidential client
- optionally `bearer_token` if your X setup needs a separate app bearer for `search/all`
- the X account you will authorize
- a Git remote pointing at your private GitHub repo

## Local setup

```bash
uv sync --dev
uv run xsync init-config
```

Fill in `.xsync/config.toml`, then authenticate:

```bash
uv run xsync auth
```

Run a full sync:

```bash
uv run xsync sync all
```

## Config file

`xsync init-config` creates `.xsync/config.toml`:

```toml
[x]
client_id = "..."
client_secret = ""
bearer_token = ""
redirect_uri = "http://127.0.0.1:8787/callback"
scopes = ["tweet.read", "users.read", "bookmark.read", "offline.access"]

[git]
remote = "origin"
branch = "main"
auto_push = true
```

Environment variables override file values:

- `XSYNC_CLIENT_ID`
- `XSYNC_CLIENT_SECRET`
- `XSYNC_BEARER_TOKEN`
- `XSYNC_REDIRECT_URI`
- `XSYNC_SCOPES`
- `XSYNC_GIT_REMOTE`
- `XSYNC_GIT_BRANCH`
- `XSYNC_AUTO_PUSH`

## Output layout

- `x-posts/<post_id>.md`: canonical post documents
- `x-bookmarks/<post_id>.md`: bookmark lifecycle docs pointing to canonical posts/threads
- `x-threads/<conversation_id>.json`: normalized conversation data
- `x-media/`: downloaded images and video/GIF preview images
- `x-sync-runs/<timestamp>.json`: sync manifests
- `.xsync/state.db`: local state, tokens, cursors, and run records

`.xsync/` stays out of Git by default.

## API constraints

- Bookmarks API returns only the current 800 most recent bookmarks.
- `xsync` records the first and last time it observed a bookmark because X does not expose the original bookmark timestamp.
- Authored-post backfill and thread hydration use `search/all`, so they consume paid API credits.
- Videos and GIFs are stored as metadata plus source URLs and preview images, not full binaries.

## Systemd

Example unit files are in [`systemd/xsync.service`](/Users/uwe/dev/x/xsync/systemd/xsync.service) and [`systemd/xsync.timer`](/Users/uwe/dev/x/xsync/systemd/xsync.timer).

## Testing

```bash
uv run pytest
uv run ruff check
```
