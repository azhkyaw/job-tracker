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

The browser extension (Chrome on Windows) points at `http://127.0.0.1:8000`
with the `TRACKER_API_TOKEN` from `.env` — same value, same as the Linux flow.

## Gmail OAuth (first-time setup)

The CLI's `auth` command (`pipeline/gmail_sync.py:get_service`) does a
`run_local_server(port=0, open_browser=False)` dance: it binds a listener on
a random `127.0.0.1` port, prints an authorization URL instead of launching a
browser itself, and waits for Google to redirect back to that port with the
code. This works fine on native Windows — no WSL localhost quirks — but the
Cloud Console side isn't self-explanatory the first time through.

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
.\.venv\Scripts\python.exe -m pipeline.cli auth
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
side is broken — ATS-domain misses are the expected first-run gap, not an
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
