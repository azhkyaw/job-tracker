-- 005_posting_ats.sql — track which ATS (Greenhouse, Lever, Workday, ...)
-- manages applications for a posting. The email pipeline's stage-2 extraction
-- already identifies this from ATS-sent confirmation mail (email_classifier.py
-- Extraction.ats) but never persisted it anywhere queryable — this gives it a
-- home on the posting it was learned from.

BEGIN;

ALTER TABLE postings
    ADD COLUMN ats text;

COMMIT;
