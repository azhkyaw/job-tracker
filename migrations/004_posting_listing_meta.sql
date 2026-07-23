-- 004_posting_listing_meta.sql — capture the listing-level metadata LinkedIn
-- (and possibly other platforms) show next to the job title: where it's
-- based, how long ago it was posted, and whether it's a repost.
--
-- posted_label is stored as the platform's own relative-time text ("3 weeks
-- ago") rather than parsed into a timestamp: the granularity is ambiguous
-- (a "month" is fuzzy) and captured_at already anchors when we saw it, so
-- computing a derived date would be false precision. Table-level GRANT/RLS
-- from 003_multi_tenant.sql already cover new columns on an existing table.

BEGIN;

ALTER TABLE postings
    ADD COLUMN location     text,
    ADD COLUMN posted_label text,
    ADD COLUMN reposted     boolean;

COMMIT;
