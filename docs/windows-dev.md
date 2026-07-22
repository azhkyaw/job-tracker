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

> `scripts/env.ps1` still exists for setting vars in a shell explicitly, but
> with `.env` auto-loading you no longer need to dot-source it.

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
