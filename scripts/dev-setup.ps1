# One-time native-Windows setup: Python env via uv, Postgres via Docker, and
# the 'tracker' dev database initialized. Idempotent — safe to re-run; it skips
# the DB init if 'tracker' already has tables.
#
#   pwsh scripts/dev-setup.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

foreach ($tool in 'uv', 'docker') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool not found on PATH. Install it first (uv: https://docs.astral.sh/uv/  docker: Docker Desktop)."
    }
}

Write-Host '==> Python env (.venv) via uv'
if (-not (Test-Path (Join-Path $root '.venv'))) { uv venv --python 3.12 }
uv pip install -r requirements.txt

Write-Host '==> Postgres via Docker'
docker compose up -d

Write-Host '==> Waiting for Postgres to be ready'
$ready = $false
foreach ($i in 1..30) {
    docker compose exec -T db pg_isready -U postgres *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw 'Postgres did not become ready in time.' }

# Init the dev DB only if it has no tables yet (avoids re-running non-idempotent
# CREATE TABLE migrations).
$hasTables = (docker compose exec -T db psql -U postgres -d tracker -tAc `
    "SELECT to_regclass('public.users') IS NOT NULL;").Trim()
if ($hasTables -eq 't') {
    Write-Host '==> Dev DB "tracker" already initialized — skipping migrations'
} else {
    Write-Host '==> Initializing dev DB "tracker"'
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d tracker -f /migrations/001_init.sql
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d tracker -f /migrations/002_gmail_sync_state.sql
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d tracker -f /migrations/003_multi_tenant.sql
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d tracker -f /migrations/004_posting_listing_meta.sql
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d tracker -f /migrations/005_posting_ats.sql
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d tracker `
        -c "INSERT INTO users (email) VALUES ('you@example.com');"
}

Write-Host ''
Write-Host 'Setup complete. Next:'
Write-Host '  pwsh scripts/test.ps1            # run the suites'
Write-Host '  .\.venv\Scripts\python.exe -m pipeline.cli serve   # UI at http://127.0.0.1:8000'
