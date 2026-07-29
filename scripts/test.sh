#!/usr/bin/env bash
# Full test run on a throwaway DB. Suites stub all LLM/embedding calls — no
# API cost. Order matters: test_phase4 LAST (it creates a second user, which
# kills the legacy-token fallback test_captures relies on).
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${TRACKER_TEST_DB:-tracker_test}"
dropdb --if-exists "$DB"
createdb "$DB"
psql "$DB" -q -v ON_ERROR_STOP=1 \
  -f migrations/001_init.sql \
  -f migrations/002_gmail_sync_state.sql \
  -f migrations/003_multi_tenant.sql \
  -f migrations/004_posting_listing_meta.sql \
  -f migrations/005_posting_ats.sql \
  -f migrations/006_user_timezone.sql \
  -f migrations/007_user_theme.sql \
  -f migrations/008_application_origin.sql \
  -f migrations/009_application_answers.sql \
  -f migrations/010_answer_occurrence.sql \
  -f migrations/011_posting_salary.sql \
  -c "INSERT INTO users (email) VALUES ('dev@test.local');" 2>/dev/null
export TRACKER_DATABASE_URL="postgresql:///$DB"
export TRACKER_API_TOKEN=testtok
export TRACKER_SECRET_KEY=test-secret
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-test-dummy-key}"
for t in test_integration test_web test_captures test_phase3 test_email_ingest test_phase4; do
  printf "== %-18s " "$t"
  if python3 "tests/$t.py" > "/tmp/$t.log" 2>&1; then
    echo PASS
  else
    echo FAIL
    tail -25 "/tmp/$t.log"
    exit 1
  fi
done
echo "ALL SUITES PASS"
