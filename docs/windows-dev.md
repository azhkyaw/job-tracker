# Running on native Windows (no WSL)

The repo's docs assume WSL/Linux (`scripts/test.sh`, `sudo service postgresql
start`, Unix-socket `postgresql:///tracker`). This file is the native-Windows
equivalent, using **Docker** for Postgres and **uv** for Python. Nothing in the
app code changes — the DB layer is URL-driven (`pipeline/config.py`), so the
only substitution is *how* Postgres and Python are provided.

## Prerequisites

- **Docker Desktop** (provides Postgres + pgvector + pg_trgm via the
  `pgvector/pgvector:pg15` image — no local Postgres install, no compiling
  pgvector on Windows).
- **uv** (`https://docs.astral.sh/uv/`) — manages Python 3.12 and the venv.
  You do **not** need Python on PATH; uv fetches it.

## One-time setup

```powershell
pwsh scripts/dev-setup.ps1
```

This creates `.venv` (Python 3.12 + `requirements.txt`), starts the Docker
Postgres, waits for it to be healthy, and initializes the `tracker` dev
database (migrations 001–003 + a seed user). Idempotent — safe to re-run.

## Run the tests

```powershell
pwsh scripts/test.ps1
```

Native equivalent of `scripts/test.sh`: throwaway `tracker_test` DB, all five
suites in the required order (`test_phase4` last). `psql` runs *inside* the
container against the mounted `/migrations`, so no Postgres client is needed on
Windows. All LLM/embedding calls are stubbed — zero API cost.

## Run the app

Config comes from a gitignored **`.env`** at the project root, auto-loaded by
`pipeline/config.py` (real shell env vars still override it). `dev-setup.ps1`
assumes it exists; copy it from the template and fill in your Anthropic key:

```powershell
copy .env.example .env      # then edit: set ANTHROPIC_API_KEY, keep the rest
```

Then just run the CLI directly — no per-shell env setup needed:

```powershell
.\.venv\Scripts\python.exe -m pipeline.cli serve    # UI at http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m pipeline.cli work --once
.\.venv\Scripts\python.exe -m pipeline.cli status
```

`uv run python -m pipeline.cli <cmd>` is equivalent and shorter — with no
`pyproject.toml` in the repo, uv acts as venv+pip rather than a project manager
(so `uv sync` / `uv add` don't apply here), and `uv run` picks up the
repo-root `.venv` on its own.

The browser extension (Chrome on Windows) points at `http://127.0.0.1:8000`
with the `TRACKER_API_TOKEN` from `.env` — same value, same as the Linux flow.

## Gmail (IMAP + app password — the default)

No Google Cloud project, no OAuth consent screen. Requires 2-Step
Verification on your Google account (Advanced Protection and
security-key-only 2SV accounts can't generate app passwords — use the OAuth
alternative below instead).

```powershell
# Generate one at https://myaccount.google.com/apppasswords first.
.\.venv\Scripts\python.exe -m pipeline.cli auth
```

Prompts for your Gmail address (Enter accepts the tracker account's own
email) and the app password (hidden input — this isn't a real TTY-less
`!`-prefixed command, so run it from an actual PowerShell/Terminal window,
not through an automation harness that can't attach interactive stdin). It
verifies the credential by connecting before storing anything, so a typo
fails immediately with a message telling you what's wrong rather than
storing a broken credential.

```powershell
.\.venv\Scripts\python.exe -m pipeline.cli backfill -m 12
.\.venv\Scripts\python.exe -m pipeline.cli work --once
.\.venv\Scripts\python.exe -m pipeline.cli serve   # check /triage for results
```

If `backfill` reports 0 candidates, check `ALLOWLIST_DOMAINS` in
`pipeline/config.py` against your real ATS senders before assuming IMAP is
broken — ATS-domain misses are the expected first-run gap, not an auth
failure.

### Gmail OAuth (alternative — Workspace / Advanced Protection accounts)

The CLI's `auth --oauth` command (`pipeline/gmail_sync.py:get_service`) does a
`run_local_server(port=0, open_browser=False)` dance: it binds a listener on
a random `127.0.0.1` port, prints an authorization URL instead of launching a
browser itself, and waits for Google to redirect back to that port with the
code. This works fine on native Windows — no WSL localhost quirks — but the
Cloud Console side isn't self-explanatory the first time through. Also read
`docs/email-ingest.md` §8 and the 7-day-token-expiry gotcha in `CLAUDE.md`
before relying on this for anything long-running.

**1. Google Cloud Console — one-time project setup**
([console.cloud.google.com](https://console.cloud.google.com)):

1. Create a project (or pick an existing one).
2. **APIs & Services → Library** → enable **Gmail API**.
3. **APIs & Services → OAuth consent screen**:
   - User type **External** (unless you're on a Google Workspace org and want Internal).
   - Fill in app name + your email as support/developer contact.
   - **Scopes** → add `https://www.googleapis.com/auth/gmail.readonly`.
   - Leave publishing status as **Testing**.
   - **Test users** → add the Gmail address(es) you'll actually sync. This step is
     required even for your own account — Testing-status apps reject sign-in
     from anyone not on this list, unverified-sensitive-scope apps included.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type **Desktop app** → Create → Download JSON.
   - Rename the downloaded file to `credentials.json` and place it at the repo
     root (`C:\projects\job-tracker\credentials.json`) — `config.py` resolves
     it relative to the working directory, so run the CLI from repo root.
   - (Multi-user web-connect flow only: also create a **Web application**
     client with redirect URI `<TRACKER_BASE_URL>/oauth/gmail/callback` —
     `http://127.0.0.1:8000/oauth/gmail/callback` for local dev — save as
     `credentials-web.json` at repo root. Skip this for single-user CLI testing.)

**2. Run the CLI flow from PowerShell, repo root:**

```powershell
.\.venv\Scripts\python.exe -m pipeline.cli auth --oauth
```

- It prints `Please visit this URL to authorize this application: <url>` and
  hangs — it does **not** open a browser for you (`open_browser=False`).
  Copy the full URL into your browser manually.
- Sign in with the Gmail account you added as a test user. Google shows an
  **"Google hasn't verified this app"** interstitial — this is expected for a
  Testing-status app; click **Advanced → Go to \<app name\> (unsafe)** to continue.
- **Windows Defender Firewall may prompt** ("Windows Defender Firewall has
  blocked some features of python.exe") when the loopback listener binds —
  allow access on **Private networks**, otherwise the redirect never reaches
  the waiting process and `auth` hangs indefinitely.
- After granting access, the browser shows "The authentication flow has
  completed. You may close this window." — that tab is done; switch back to
  the terminal, which should now print `OK — token cached at .gmail_token.json`.

**3. Verify and proceed:**

```powershell
Test-Path .gmail_token.json   # should be True — gitignored, don't commit it
.\.venv\Scripts\python.exe -m pipeline.cli backfill -m 12
.\.venv\Scripts\python.exe -m pipeline.cli work --once
.\.venv\Scripts\python.exe -m pipeline.cli serve   # check /triage for results
```

If `backfill` reports 0 candidates, check `ALLOWLIST_DOMAINS` in
`pipeline/config.py` against your real ATS senders before assuming the OAuth
flow is broken — ATS-domain misses are the expected first-run gap, not an
auth failure.

## Key differences from the Linux/WSL docs

| Linux/WSL doc says            | Windows equivalent                                   |
|-------------------------------|------------------------------------------------------|
| `sudo service postgresql start` | `docker compose up -d`                             |
| `postgresql:///tracker` (socket) | `postgresql://postgres:postgres@localhost:55432/tracker` (TCP) |
| `./scripts/test.sh`           | `pwsh scripts/test.ps1`                              |
| `createdb` / `psql` on PATH   | run inside the container via `docker compose exec`   |
| `python3 tests/...`           | `.\.venv\Scripts\python.exe tests/...`               |

**Host port is 55432**, not 5432, to avoid colliding with other local Postgres
containers/installs. Override with `$env:TRACKER_DB_PORT` before
`docker compose up` (also honored by `test.ps1`).

## Stopping / resetting

```powershell
docker compose down       # stop, keep data
docker compose down -v    # stop, wipe the database volume
```

## Managed Postgres (running the app from more than one machine)

The dev DB can live on a managed provider (Neon, in this project's case)
instead of the local Docker container, so the same data is reachable from any
machine. Local Docker Postgres is still used for `scripts/test.ps1`'s
throwaway `tracker_test` DB — only the dev database needs to move.

- **Extensions:** the schema needs `pgvector` + `pg_trgm` (both supported by
  Neon; `CREATE EXTENSION` runs as part of `001_init.sql`).
- **Use the DIRECT (unpooled) connection string, not `-pooler`.**
  `pipeline/db.py:connect_scoped` does `SET ROLE tracker_app` +
  `SET app.user_id` on a plain `psycopg.connect()` per request, for RLS. If a
  later statement in that same request lands on a different backend — which
  Neon's pooler (PgBouncer, transaction mode) can do between separate
  transactions on one client connection — the `SET ROLE` silently doesn't
  carry over. Because `application_status`'s RLS policy fails closed on a
  NULL `app.user_id` (invariant #2), the failure mode is empty results, not
  an error. This app is single-user/low-traffic, so skipping the pooler costs
  nothing.
- **Bring up schema before data**, the same order `dev-setup.ps1` uses
  locally — don't `pg_dump` the schema from Docker and restore it as-is,
  since that dump won't recreate the `tracker_app` role migration `003`
  creates, and restore will fail on the GRANT statements that reference it:

  ```bash
  export MSYS_NO_PATHCONV=1   # Git Bash mangles /migrations/... otherwise
  for f in migrations/*.sql; do
    docker compose exec -T db psql "$NEON_URL" -v ON_ERROR_STOP=1 -f "/migrations/$(basename "$f")"
  done
  ```

- **Then copy data only** (schema already applied above):

  ```bash
  docker compose exec -T db pg_dump -U postgres -d tracker --data-only \
    | docker compose exec -T db psql "$NEON_URL" -v ON_ERROR_STOP=1
  ```

- Point `.env`'s `TRACKER_DATABASE_URL` at the direct connection string
  (`...neon.tech/<db>?sslmode=require`) and verify with
  `python -m pipeline.cli status`.
- `.env` is gitignored and per-machine — copy it (same `TRACKER_SECRET_KEY`,
  `TRACKER_API_TOKEN`, `ANTHROPIC_API_KEY`, new `TRACKER_DATABASE_URL`) to
  the second machine yourself; there's no other sync mechanism. Losing or
  mismatching `TRACKER_SECRET_KEY` between machines orphans encrypted Gmail
  creds stored by whichever machine wrote them. `TRACKER_INGEST_ALL` matters
  here too: it is the one flag that fails *quietly* when absent, so a second
  machine without it reverts to the candidate pre-filter and drops job mail the
  other machine ingests, against the same database.
- **`.venv` does not travel between machines either, and the error doesn't say
  so.** uv writes the base interpreter's absolute path into the venv's
  `python.exe` trampoline, so a `.venv` created under a different Windows
  profile fails with `(uv internal error) Failed to spawn the python child
  process: entity not found`, and `uv run` quietly falls back to the bare
  uv-managed interpreter — where every import fails as
  `ModuleNotFoundError: No module named 'fastapi'`, which looks like a
  dependency problem rather than a broken venv. Editing `pyvenv.cfg`'s `home`
  does not help (the path is baked into the .exe). Rebuild it:
  `uv venv --clear --python 3.12` then `uv pip install -r requirements.txt`.
