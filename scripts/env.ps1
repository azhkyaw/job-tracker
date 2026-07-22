# Dot-source this to load dev env vars into the current shell:  . .\scripts\env.ps1
# Dev defaults only — no real secrets. Point the app at the Docker Postgres.
$env:TRACKER_DATABASE_URL = 'postgresql://postgres:postgres@localhost:55432/tracker'  # host port from docker-compose.yml
$env:TRACKER_SECRET_KEY   = 'dev-secret-change-me'   # dev only; losing it only orphans dev Gmail creds
$env:TRACKER_API_TOKEN    = 'dev-extension-token'    # paste this into the extension's Options page

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Warning 'ANTHROPIC_API_KEY is not set — LLM calls (work/backfill) will fail. Set it: $env:ANTHROPIC_API_KEY = "sk-ant-..."'
}
Write-Host 'Dev env loaded. Run the app with: .\.venv\Scripts\python.exe -m pipeline.cli <serve|work|backfill|status>'
