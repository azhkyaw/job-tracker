-- 003_multi_tenant.sql — Phase 4 (design doc §12): accounts, sessions, and
-- row-level security.
--
-- Tenancy model:
--   * The app's admin connection (table owner) handles auth bootstrap only:
--     users and sessions lookups, signup, login, and trusted batch work
--     (worker, gmail sync).
--   * Every request-serving data query runs as the NOLOGIN role tracker_app
--     with app.user_id set — RLS then scopes every data table even if a
--     query forgets its WHERE user_id. current_setting(..., true) returns
--     NULL when unset, and NULL = user_id matches nothing: fail closed.

BEGIN;

ALTER TABLE users
    ADD COLUMN password_hash     text,
    ADD COLUMN api_token_hash    text,     -- sha256 hex of the per-user token
    ADD COLUMN resume_profile    text,     -- cover-letter grounding, per user
    ADD COLUMN gmail_credentials text,     -- Fernet-encrypted OAuth token JSON
    ADD COLUMN oauth_state       text;     -- in-flight Gmail OAuth CSRF state

CREATE TABLE sessions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

-- ---------------------------------------------------------------- RLS role
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tracker_app') THEN
        CREATE ROLE tracker_app NOLOGIN;
    END IF;
    EXECUTE format('GRANT tracker_app TO %I', session_user);
END $$;

GRANT USAGE ON SCHEMA public TO tracker_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    jobs, postings, applications, events, extractions, contacts,
    artifacts, emails, duplicate_candidates, job_queue, gmail_sync_state
TO tracker_app;
GRANT SELECT ON application_status TO tracker_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tracker_app;
-- Deliberately NO grants on users or sessions: auth data is admin-only.

-- ---------------------------------------------------------------- policies
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'jobs','postings','applications','events','extractions','contacts',
        'artifacts','emails','duplicate_candidates','job_queue','gmail_sync_state']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format($p$
            CREATE POLICY tenant_isolation ON %I
            USING      (user_id = current_setting('app.user_id', true)::uuid)
            WITH CHECK (user_id = current_setting('app.user_id', true)::uuid)
        $p$, t);
    END LOOP;
END $$;

-- Views execute with the view owner's privileges by default, which would
-- BYPASS RLS for anyone allowed to select from the view. security_invoker
-- (PG15+) makes the view honor the caller's RLS context.
ALTER VIEW application_status SET (security_invoker = true);

COMMIT;
