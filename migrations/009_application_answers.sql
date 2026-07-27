-- 009_application_answers.sql — what the application FORM asked and what the
-- user answered (LinkedIn Easy Apply's screening questions today; any in-page
-- apply form the extension can see, later).
--
-- Its own table rather than an events row or an artifacts blob for one reason:
-- the value isn't only "what did I tell THIS employer" (the detail page), it's
-- "what do I usually answer for THIS question" across every application — a
-- per-question grouping, which needs a column to group by. question_norm is
-- that column; pipeline/answers.py:norm_question is its single source of truth
-- (same rule as norm_company, invariant #4 — never reimplement it in SQL).
--
-- UNIQUE (application_id, question_norm): re-capturing the same job overwrites
-- the previous answer instead of stacking near-duplicates. A later capture is
-- by definition the more recent thing the user told them.

BEGIN;

CREATE TABLE application_answers (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    application_id  uuid NOT NULL REFERENCES applications(id),
    -- Which captured ad's form this came from. Nullable: the answer outlives
    -- the posting's provenance being interesting, and dedup re-points postings.
    posting_id      uuid REFERENCES postings(id),
    question        text NOT NULL,      -- as shown, for display
    question_norm   text NOT NULL,      -- for grouping across applications
    answer          text NOT NULL,
    field_type      text,               -- text | textarea | select | radio | checkbox | number
    ordinal         integer,            -- order within the form, for display
    captured_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (application_id, question_norm)
);

CREATE INDEX application_answers_application_idx ON application_answers (application_id);
CREATE INDEX application_answers_norm_idx        ON application_answers (user_id, question_norm);

-- Invariant #6: every new data table needs the grant, RLS, and the policy.
GRANT SELECT, INSERT, UPDATE, DELETE ON application_answers TO tracker_app;
ALTER TABLE application_answers ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON application_answers
    USING      (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

COMMIT;
