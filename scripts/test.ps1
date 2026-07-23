# Native-Windows equivalent of scripts/test.sh. Same throwaway DB, same suite
# order (test_phase4 LAST — it creates a second user, killing the legacy-token
# fallback that test_captures relies on). Requires: docker compose up -d, and a
# .venv built by scripts/dev-setup.ps1 (falls back to `python` on PATH).
#
#   pwsh scripts/test.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$DB = if ($env:TRACKER_TEST_DB) { $env:TRACKER_TEST_DB } else { 'tracker_test' }

# Python: prefer the uv-managed venv, else whatever `python` is on PATH.
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

# psql runs INSIDE the container against the mounted /migrations — no Postgres
# client needed on Windows.
function Psql {
    param([string[]]$PsqlArgs)
    docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 @PsqlArgs
    if ($LASTEXITCODE -ne 0) { throw "psql failed: $($PsqlArgs -join ' ')" }
}

Write-Host "Resetting $DB ..."
Psql @('-d', 'postgres', '-c', "DROP DATABASE IF EXISTS $DB;")
Psql @('-d', 'postgres', '-c', "CREATE DATABASE $DB;")
Psql @('-d', $DB, '-f', '/migrations/001_init.sql')
Psql @('-d', $DB, '-f', '/migrations/002_gmail_sync_state.sql')
Psql @('-d', $DB, '-f', '/migrations/003_multi_tenant.sql')
Psql @('-d', $DB, '-f', '/migrations/004_posting_listing_meta.sql')
Psql @('-d', $DB, '-f', '/migrations/005_posting_ats.sql')
Psql @('-d', $DB, '-c', "INSERT INTO users (email) VALUES ('dev@test.local');")

$dbPort = if ($env:TRACKER_DB_PORT) { $env:TRACKER_DB_PORT } else { '55432' }
$env:TRACKER_DATABASE_URL = "postgresql://postgres:postgres@localhost:$dbPort/$DB"
$env:TRACKER_API_TOKEN    = 'testtok'
$env:TRACKER_SECRET_KEY   = 'test-secret'
if (-not $env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY = 'test-dummy-key' }

# From here, key pass/fail off the process exit code only. Under 'Stop',
# PowerShell 5.1 promotes any native-command stderr line (e.g. a harmless
# deprecation warning) to a terminating error — so drop to 'Continue' and
# merge stderr into the per-suite log.
$ErrorActionPreference = 'Continue'
$suites = 'test_integration', 'test_web', 'test_captures', 'test_phase3', 'test_phase4'
foreach ($t in $suites) {
    Write-Host ("== {0,-18} " -f $t) -NoNewline
    $log = Join-Path $env:TEMP "$t.log"
    & $py "tests/$t.py" 2>&1 | Out-File -FilePath $log -Encoding utf8
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'PASS'
    } else {
        Write-Host 'FAIL'
        Get-Content $log -Tail 25
        exit 1
    }
}
Write-Host 'ALL SUITES PASS'
