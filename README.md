# xsync

`xsync` is a Python 3.14+ CLI that archives your X posts and bookmarks into Git-friendly files, keeps sync state in SQLite, hydrates bookmarked author threads, downloads image attachments locally, and can auto-commit/push to a private GitHub repo after successful runs.

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

## Separate archive repo

`xsync` writes archive files into the current working tree, then commits and pushes that same repo.

If you want the code and the archive to live in different repos, use this layout:

1. Keep the source repo in one location, for example `/opt/xsync`.
2. Create a second private repo, for example `x-archive`, and clone it somewhere else, for example `/srv/x-archive`.
3. Install or sync `xsync` from the source repo.
4. Run `xsync` while your current directory is the archive repo.

Example:

```bash
# code repo
git clone [email protected]:uwe-schwarz/xsync.git /opt/xsync
cd /opt/xsync
uv sync

# archive repo
git clone [email protected]:you/x-archive.git /srv/x-archive
cd /srv/x-archive
/opt/xsync/.venv/bin/xsync init-config
/opt/xsync/.venv/bin/xsync auth
/opt/xsync/.venv/bin/xsync sync all
```

In `/srv/x-archive/.xsync/config.toml`, the `[git]` section should point at the archive repo remote, typically:

```toml
[git]
remote = "origin"
branch = "main"
auto_push = true
```

That means:

- the `xsync` repo stores the application code
- the `x-archive` repo stores the generated posts, bookmarks, threads, media, and manifests
- automatic commits and pushes happen in the archive repo, not the code repo

## User-wide install with uv

Yes. `uv` can install tools user-wide from a Git repo, so you do not need to clone the code repo onto the target machine if you prefer a simpler setup.

For a public repo:

```bash
uv tool install git+https://github.com/uwe-schwarz/xsync.git
```

For a private GitHub repo, SSH is usually simplest:

```bash
uv tool install git+ssh://[email protected]/uwe-schwarz/xsync.git
```

Then run it from inside the archive repo:

```bash
cd /srv/x-archive
xsync init-config
xsync auth
xsync sync all
```

To update the installed tool later:

```bash
uv tool upgrade xsync
```

If `uv` cannot authenticate to a private GitHub repo, either:

- use SSH keys for GitHub
- or configure GitHub credentials with `gh auth login`

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
- `xsync` treats bookmarks and posts as append-only backup inputs. Once an item is archived, later syncs do not re-scan older pages to refresh lifecycle state.
- Authored-post sync fetches original authored posts only and excludes replies/reposts.
- Bookmark thread hydration keeps the bookmarked post plus same-author continuation posts that directly reply to that kept chain, and excludes side-branch replies.
- Initial authored-post backfill uses `search/all`. Later post syncs switch to the user timeline with `since_id`, which is much cheaper.
- Bookmark sync stops paging as soon as it encounters the first already-archived bookmark.
- Bookmark thread hydration reuses cached thread documents and only expands from newly seen bookmark seeds.
- Bookmark thread hydration still uses `search/all`, so it consumes paid API credits, but only for newly discovered bookmark threads.
- Videos and GIFs are stored as metadata plus source URLs and preview images, not full binaries.

## Systemd

Example unit files are in [`systemd/xsync.service`](/Users/uwe/dev/x/xsync/systemd/xsync.service) and [`systemd/xsync.timer`](/Users/uwe/dev/x/xsync/systemd/xsync.timer).

## Testing

```bash
uv run pytest
uv run ruff check
```
