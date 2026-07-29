-- 010_answer_occurrence.sql — let one application hold the SAME question more
-- than once.
--
-- Migration 009 keyed answers UNIQUE (application_id, question_norm) on the
-- assumption that a repeated question means the user corrected their answer,
-- so the later one should win. A real LinkedIn Easy Apply form broke that
-- assumption on 27 Jul 2026: its work-history section is a REPEATER — one
-- "Industry", "City", "Month of From" per employer you list. Those are not one
-- question asked twice, they are N questions that happen to share a label, and
-- every entry after the first silently overwrote the one before it. Of a
-- multi-entry work history exactly one row survived per label.
--
-- `occurrence` is that entry's index within the form, counted per question:
-- the first "Industry" on the page is 0, the second is 1. It is NOT a version
-- number and never grows across captures — re-capturing the same job still
-- overwrites occurrence 0 with what you said this time (pipeline/answers.py
-- also deletes occurrences the newer capture no longer has, so a form filled
-- with fewer entries doesn't leave orphans behind).
--
-- question_norm keeps doing its real job unchanged: /answers still groups
-- every "Industry" the user has ever been asked into one bank row, across
-- applications AND across repeat entries within one form. That grouping is the
-- point of the feature, which is why the occurrence went into the key rather
-- than into the question text.

BEGIN;

ALTER TABLE application_answers
    ADD COLUMN occurrence integer NOT NULL DEFAULT 0;

ALTER TABLE application_answers
    DROP CONSTRAINT application_answers_application_id_question_norm_key;

ALTER TABLE application_answers
    ADD CONSTRAINT application_answers_application_id_question_norm_occurrence_key
    UNIQUE (application_id, question_norm, occurrence);

COMMIT;
