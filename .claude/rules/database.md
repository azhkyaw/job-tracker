---
paths:
  - "migrations/**"
  - "pipeline/db.py"
  - "docker-compose.yml"
  - "scripts/dev-setup.ps1"
  - "scripts/test.ps1"
  - "scripts/test.sh"
  - "README.md"
  - "docs/windows-dev.md"
---

# Database: migrations, the status view, Postgres on two machines

Moved here VERBATIM from CLAUDE.md on 9 Sep 2026 so it loads when Claude reads a
matching file instead of in every session (it can also be Read directly).
Dates are the key to each case; the record itself is in the database. The
migration invariant (#8) and the tenancy checklist (#6) are still in CLAUDE.md.

## Gotchas learned the hard way

- **Git Bash mangles `/migrations/...`-style paths** in `docker compose exec` commands
  (rewrites the leading `/` to the Git install dir). Prefix with `MSYS_NO_PATHCONV=1`.
- **Managed Postgres (e.g. Neon) needs the DIRECT/unpooled connection
  string, not the pooler one.** `pipeline/db.py:connect_scoped` does
  `SET ROLE tracker_app` + `SET app.user_id` on a plain per-request
  `psycopg.connect()` for RLS; a transaction-mode pooler (Neon's default)
  can hand a later statement on the same client connection a different
  backend, silently dropping the `SET ROLE` — RLS then fails closed (empty
  results, not an error). Migrating in also can't be a straight
  `pg_dump`/restore of schema+data: GRANTs reference the `tracker_app`
  role, which doesn't exist on a fresh instance — apply `migrations/*.sql`
  first, then `pg_dump --data-only`. Full procedure in
  `docs/windows-dev.md` "Managed Postgres" (dev DB moved here 2 Aug 2026;
  local Docker still backs `scripts/test.ps1`'s throwaway DB).
- **Migration filenames are hardcoded in FOUR places — there is no runner.**
  Adding `migrations/NNN_x.sql` also means editing `scripts/dev-setup.ps1`,
  `scripts/test.ps1`, `scripts/test.sh`, and the `psql -f` command block in
  `README.md`. Miss one and `test.ps1` resets its DB without the new column,
  so every page needing it 500s with no obvious cause. The dev DB is a fifth
  place, in a different sense: it is not reset between runs, so apply the file
  to it by hand (`MSYS_NO_PATHCONV=1 docker compose exec -T db psql -U postgres
  -d tracker -f /migrations/NNN_x.sql`) or the running dev server 500s while
  the suites stay green.
- **Widening `events.type`'s `CHECK` constraint means finding its
  auto-generated name first.** An inline `CHECK` on a column has no name of
  your choosing — Postgres calls it `<table>_<col>_check`
  (`events_type_check`); confirm via `\d events` before
  `DROP CONSTRAINT`/`ADD CONSTRAINT`. First time a migration needed this
  (012) — every prior migration only ever added columns/tables.
- **`application_status`'s CASE precedence uses gapped values** (multiples
  of 10: 10/30/40/45/50/60/70), not consecutive integers, specifically so a
  new event type can slot in between two existing ones without renumbering
  everything else — done once already for `engaged` (migration 012).
- **Testing an `application_status` precedence tie:** two manual events
  filed with the SAME `occurred_on` date land at the EXACT same instant
  (`ingest.local_date_to_utc` anchors every bare date to local noon), so
  that's how to provoke a real CASE tie-break in a test rather than relying
  on recency.
- **`application_status`'s `ORDER BY` had recency as the PRIMARY sort key and
  precedence as only a tiebreaker — backwards from what invariant #2 and the
  gapped CASE values imply.** Caught on a real application (Tailspin
  Consulting, 2 Aug 2026): `interview_invite` landed, then a later
  lower-ranked `confirmation` email (a technical-assessment receipt) landed
  a week after, and because the view sorted by `occurred_at DESC` first, the
  newer-but-lower-ranked row won outright — status silently regressed from
  "interviewing" to "applied". Fixed in migration 013 by swapping the sort
  key order: precedence first, `occurred_at` only breaks ties between events
  of EQUAL precedence (same-instant manual entries, per the tie-break gotcha
  above). Since `application_status` is a pure view, the fix retroactively
  corrected every application's derived status with no backfill needed —
  the general lesson invariant #2's "current status is DERIVED" is meant to
  buy you.
