-- 011_posting_salary.sql — what the AD says it pays, plus the two other
-- structured facts the platform prints beside it.
--
-- Why not `extractions`: those columns (salary_min/max/currency) are LLM output
-- read out of `jd_text`, versioned by prompt and re-runnable. The platform's
-- salary line is NOT in the ad body — it is chrome around it — so the extractor
-- has never been able to see it, and every job whose pay is printed plainly on
-- the page has come back with a null salary. Storing a stated FACT in the same
-- columns as an inferred GUESS would also destroy the ability to tell them
-- apart, and re-running a prompt could silently change a number the employer
-- published. Same separation as postings.company_raw vs jobs.company_norm.
--
-- salary_period is mandatory to the meaning, not decoration: SEA ads quote
-- MONTHLY figures where most of the world quotes annual, and this project's own
-- answer bank already holds "10800" (monthly) beside "128000" (annual) for the
-- same person. A salary without its period is not a number, it's a trap.
--
-- Nothing is NOT NULL: "Competitive salary" is a real and common ad, and it
-- must store as salary_raw with nulls beside it rather than failing a capture.

BEGIN;

ALTER TABLE postings
    ADD COLUMN salary_raw      text,      -- exactly as printed, always kept
    ADD COLUMN salary_min      numeric,
    ADD COLUMN salary_max      numeric,
    ADD COLUMN salary_currency text,      -- ISO-ish: SGD, MYR, IDR, AUD, PHP, THB
    ADD COLUMN salary_period   text,
    -- "Full time" | "Contract/Temp" | "Part time" — the platform's own wording.
    ADD COLUMN work_type       text,
    -- SEEK's "Profile salary match" badge. A fact about the fit between the ad
    -- and the user's stored profile salary, not about the job; NULL means the
    -- platform said nothing, which is different from "no match".
    ADD COLUMN salary_match    boolean;

ALTER TABLE postings
    ADD CONSTRAINT postings_salary_period_check
    CHECK (salary_period IS NULL OR salary_period IN
           ('hourly', 'daily', 'weekly', 'monthly', 'annual'));

-- A range that runs backwards is a parser bug, not data — refuse it here so it
-- can never quietly reach analytics.
ALTER TABLE postings
    ADD CONSTRAINT postings_salary_range_check
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max);

COMMIT;
