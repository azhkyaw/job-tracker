-- 014_application_resume_file.sql — record WHICH resume was sent with an
-- application, as a field rather than as a fake screening question.
--
-- LinkedIn's Easy Apply resume picker is a radio group, and the selected
-- radio's accessible name is the action available on it: "Deselect resume
-- <file>.pdf". The answer sweep recorded it like any other radio, so
-- `application_answers` accumulated 24 rows whose question and answer were the
-- SAME string, across two groups named after the two PDFs. In /answers — a
-- view whose whole point is "the same question, asked by many employers" —
-- that reads as two questions nobody ever asked.
--
-- The data underneath is worth keeping though, and is arguably the better
-- version of what `applications.focused` was meant to measure: focused came
-- out a constant (46 of 46 'generic', so analytics.by_focus renders nothing),
-- while which-resume-did-I-send is a real two-way split across 18 real
-- applications, already captured for free.
--
-- Nullable with no default and no backfill in this file: an application whose
-- form had no resume picker (external ATS, manual entry, email-only) genuinely
-- has no answer here, and NULL is that. Existing rows are backfilled out of
-- application_answers separately.

BEGIN;

ALTER TABLE applications ADD COLUMN resume_file text;

COMMENT ON COLUMN applications.resume_file IS
  'Filename of the resume selected on the apply form, parsed from the '
  'platform''s "(De)select resume <file>" control by pipeline/answers.py. '
  'NULL when the form had no picker.';

COMMIT;
