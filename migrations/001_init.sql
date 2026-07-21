-- 001_init.sql — Job Application Tracker, Phase 1 schema
-- Design doc: job-tracker-design.md §7
-- Requires: PostgreSQL 15+, pgvector, pg_trgm
--
-- Conventions:
--   * Every table carries user_id from day one (multi-tenancy is a migration,
--     not a rewrite). v1 has exactly one row in users.
--   * Status is an append-only event log; current status is DERIVED via the
--     application_status view, never stored as a mutable column.
--   * company_norm is populated by app code (pipeline.email_classifier.norm_company)
--     — single source of truth for normalization lives in Python.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- users
CREATE TABLE users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email       text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- jobs
-- Canonical role after dedup; 1..n postings roll up to one job.
CREATE TABLE jobs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id),
    company_norm     text NOT NULL,
    title_canonical  text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX jobs_user_company_idx ON jobs (user_id, company_norm);
CREATE INDEX jobs_company_trgm_idx ON jobs USING gin (company_norm gin_trgm_ops);

-- ---------------------------------------------------------------- postings
-- A specific ad on a specific platform. Embedding dimension 1024 is the
-- Phase 3 default (voyage-3.5-lite class); changing models = ALTER + re-embed.
CREATE TABLE postings (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id),
    job_id           uuid REFERENCES jobs(id),          -- set by dedup / creation
    platform         text NOT NULL CHECK (platform IN ('linkedin','jobstreet','indeed','other')),
    platform_job_id  text,
    url              text,
    company_raw      text,
    company_norm     text,
    title            text,
    jd_text          text,
    jd_embedding     vector(1024),
    captured_via     text NOT NULL CHECK (captured_via IN ('extension','email_only','manual')),
    captured_at      timestamptz NOT NULL DEFAULT now()
);

-- Same ad captured twice must upsert, not duplicate.
CREATE UNIQUE INDEX postings_platform_job_uidx
    ON postings (user_id, platform, platform_job_id)
    WHERE platform_job_id IS NOT NULL;
CREATE INDEX postings_user_idx         ON postings (user_id);
CREATE INDEX postings_job_idx          ON postings (job_id);
CREATE INDEX postings_company_trgm_idx ON postings USING gin (company_norm gin_trgm_ops);
CREATE INDEX postings_title_trgm_idx   ON postings USING gin (title gin_trgm_ops);
-- ANN index for dedup blocking (cheap on empty table; pgvector >= 0.5).
CREATE INDEX postings_embedding_idx    ON postings USING hnsw (jd_embedding vector_cosine_ops);

-- ---------------------------------------------------------------- applications
-- The user's action against a canonical job. One application per job:
-- dedup merges must re-point postings and move events BEFORE deleting the
-- losing job's application (worker responsibility, Phase 3).
CREATE TABLE applications (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 uuid NOT NULL REFERENCES users(id),
    job_id                  uuid NOT NULL REFERENCES jobs(id),
    applied_via_posting_id  uuid REFERENCES postings(id),
    focused                 boolean,        -- NULL = infer from artifacts
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, job_id)
);

CREATE INDEX applications_user_idx ON applications (user_id);

-- ---------------------------------------------------------------- emails
-- Raw ingested mail + classification/extraction results.
-- body_text retained in v1 for reprocessing (design doc open question #4;
-- revisit retention before multi-user).
CREATE TABLE emails (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  uuid NOT NULL REFERENCES users(id),
    gmail_message_id         text NOT NULL,
    sender                   text NOT NULL,
    subject                  text,
    body_text                text,
    received_at              timestamptz NOT NULL,
    classification           text CHECK (classification IN
                                 ('confirmation','rejection','interview_invite',
                                  'recruiter_outreach','status_update','other',
                                  'not_job_related')),
    classify_confidence      real,
    extraction               jsonb,
    matched_application_id   uuid REFERENCES applications(id),
    match_score              real,
    triage_state             text NOT NULL DEFAULT 'unprocessed'
                                 CHECK (triage_state IN
                                 ('unprocessed','auto_matched','pending','resolved','ignored')),
    model                    text,
    prompt_version           text,
    processed_at             timestamptz,
    UNIQUE (user_id, gmail_message_id)
);

CREATE INDEX emails_triage_idx ON emails (user_id, triage_state)
    WHERE triage_state IN ('unprocessed','pending');

-- ---------------------------------------------------------------- events
-- Append-only status timeline. occurred_at is real-world time (email received,
-- action taken), NOT insertion time — backfill inserts out of order safely.
CREATE TABLE events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id),
    application_id   uuid NOT NULL REFERENCES applications(id),
    type             text NOT NULL CHECK (type IN
                         ('interested','applied','confirmation','viewed',
                          'rejected','interview_invite','recruiter_outreach',
                          'follow_up_sent','offer','withdrawn','note')),
    source           text NOT NULL CHECK (source IN ('email','extension','manual','system')),
    occurred_at      timestamptz NOT NULL,
    source_email_id  uuid REFERENCES emails(id),
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX events_application_idx ON events (application_id, occurred_at);
CREATE INDEX events_user_type_idx   ON events (user_id, type);

-- ---------------------------------------------------------------- extractions
CREATE TABLE extractions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    posting_id      uuid NOT NULL REFERENCES postings(id),
    languages       text[] NOT NULL DEFAULT '{}',
    technologies    text[] NOT NULL DEFAULT '{}',
    seniority       text,
    salary_min      numeric,
    salary_max      numeric,
    currency        text,
    work_mode       text CHECK (work_mode IN ('onsite','hybrid','remote')),
    visa_signal     text CHECK (visa_signal IN ('sponsors','local_only','unclear')),
    visa_notes      text,
    verified        boolean NOT NULL DEFAULT false,
    model           text,
    prompt_version  text,
    extracted_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX extractions_posting_idx ON extractions (posting_id);

-- ---------------------------------------------------------------- contacts
CREATE TABLE contacts (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id),
    job_id         uuid NOT NULL REFERENCES jobs(id),
    name           text NOT NULL,
    role           text,
    url            text,
    source         text,
    approached     boolean NOT NULL DEFAULT false,
    approached_at  timestamptz,
    notes          text
);

CREATE INDEX contacts_job_idx ON contacts (job_id);

-- ---------------------------------------------------------------- artifacts
CREATE TABLE artifacts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    application_id  uuid NOT NULL REFERENCES applications(id),
    kind            text NOT NULL CHECK (kind IN ('cover_letter','prep_note')),
    content         text NOT NULL,
    model           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX artifacts_application_idx ON artifacts (application_id);

-- ---------------------------------------------------------------- duplicate_candidates
CREATE TABLE duplicate_candidates (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id),
    posting_a   uuid NOT NULL REFERENCES postings(id),
    posting_b   uuid NOT NULL REFERENCES postings(id),
    title_sim   real,
    cosine_sim  real,
    state       text NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('auto','confirmed','rejected','pending')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (posting_a < posting_b),               -- canonical pair ordering
    UNIQUE (posting_a, posting_b)
);

-- ---------------------------------------------------------------- job_queue
-- Postgres-backed queue; workers poll with FOR UPDATE SKIP LOCKED.
CREATE TABLE job_queue (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    type        text NOT NULL CHECK (type IN
                    ('classify_email','extract_email','extract_jd','embed_jd',
                     'dedup_scan','generate_cover_letter','reminders_scan')),
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    state       text NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending','running','done','failed','dead')),
    run_after   timestamptz NOT NULL DEFAULT now(),
    attempts    int NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX job_queue_poll_idx ON job_queue (run_after) WHERE state = 'pending';

-- ---------------------------------------------------------------- derived status
-- Current status = most recent STATUS-DRIVING event by real-world time;
-- precedence breaks same-instant ties (rejection beats the interview invite
-- it arrived with). follow_up_sent / recruiter_outreach / note never drive status.
CREATE VIEW application_status AS
SELECT a.id AS application_id,
       a.user_id,
       a.job_id,
       COALESCE(
           (SELECT e.type
            FROM events e
            WHERE e.application_id = a.id
              AND e.type IN ('interested','applied','confirmation','viewed',
                             'interview_invite','rejected','offer','withdrawn')
            ORDER BY e.occurred_at DESC,
                     CASE e.type
                         WHEN 'offer'            THEN 7
                         WHEN 'withdrawn'        THEN 6
                         WHEN 'rejected'         THEN 6
                         WHEN 'interview_invite' THEN 5
                         WHEN 'viewed'           THEN 4
                         WHEN 'confirmation'     THEN 3
                         WHEN 'applied'          THEN 3
                         ELSE 1
                     END DESC
            LIMIT 1),
           'interested') AS status
FROM applications a;

COMMIT;

-- Bootstrap the single v1 user (edit the email, then uncomment):
-- INSERT INTO users (email) VALUES ('you@example.com');
