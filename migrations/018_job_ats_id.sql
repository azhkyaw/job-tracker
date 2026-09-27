-- 018: jobs.ats_job_id, the job's own id on its hiring system
-- (docs/career-sites.md §16).
--
-- A requisition is the employer's role, not an ad, so it lives on the JOB
-- (postings != jobs != applications, invariant #3): one per job, unique per
-- user, carried across by dedup.merge_jobs. The value is the hiring-system
-- page's own posting id, `career2.successfactors.eu/51234` or
-- `contoso.wd3.myworkdayjobs.com/r200001`, the string
-- extension/shared/jobposting.js:pageId derives.
--
-- It is the key an ATS submit and its listing or job-board record meet on,
-- whichever arrives first (pipeline/ingest.py:upsert_record), and the key an
-- email naming the requisition is matched on (pipeline/matcher.py). Nullable:
-- most records were made where no hiring system was seen, and nothing is
-- backfilled here.

BEGIN;

ALTER TABLE jobs ADD COLUMN ats_job_id text;

CREATE UNIQUE INDEX jobs_ats_job_id_uidx ON jobs (user_id, ats_job_id)
    WHERE ats_job_id IS NOT NULL;

COMMIT;
