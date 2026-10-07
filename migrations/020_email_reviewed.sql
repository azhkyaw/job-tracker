-- 020_email_reviewed.sql — "Looks right" on mail the matcher filed on its own.
--
-- /triage gained a strip on 7 Oct 2026 (pipeline/triage.py:review): the
-- emails the matcher filed WITHOUT a person, where it had less to go on — a
-- record it started, a score under config.REVIEW_SCORE_BELOW, or a record
-- whose company is not the email's. Each wrong one on record was silent
-- until someone happened to open the record it landed on. The strip lists
-- them for config.REVIEW_DAYS after filing; this column is the click that
-- takes one off it.
--
-- A timestamp, not a new triage_state: 'auto_matched' against 'resolved'
-- records WHO filed an email, and the matcher's threshold replays read that
-- split (docs/worklog.md task 1). A filing a person looked at and agreed
-- with is still the matcher's.
--
-- A column on an existing table, so RLS and the tracker_app grants cover it
-- already (invariant #6).

BEGIN;

ALTER TABLE emails ADD COLUMN reviewed_at timestamptz;

COMMIT;
