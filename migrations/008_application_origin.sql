-- 008_application_origin.sql — provenance for an application: 'applied' (the
-- user acted), 'inbound' (a recruiter/employer approached the user — a lead,
-- carrying status/history but never an 'applied' event unless promoted),
-- 'saved' (captured as interesting but not yet applied to). Immutable once
-- set, distinct from derived status (application_status view) — a promoted
-- inbound lead keeps origin='inbound' even after gaining an 'applied' event.

BEGIN;

ALTER TABLE applications ADD COLUMN origin text NOT NULL DEFAULT 'applied'
    CHECK (origin IN ('applied','inbound','saved'));

-- Backfill: rows with no 'applied' event were never applications, only
-- saved/interested captures.
UPDATE applications a SET origin = 'saved'
 WHERE NOT EXISTS (SELECT 1 FROM events e
                   WHERE e.application_id = a.id AND e.type = 'applied');

COMMIT;
