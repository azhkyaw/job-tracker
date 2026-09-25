-- 017: extractions.visa_signal gains jd_extract_v2's vocabulary.
--
-- v1 answered sponsors | local_only | unclear, and folded into `local_only`
-- statements the author reads very differently: "Singapore Citizens / PRs
-- only", "sponsorship is not available", "applicants MUST BE currently based in
-- Singapore" (a pass may still be sponsored for someone already there, just no
-- hire from abroad) and "we will be prioritizing applicants with a current
-- right to work" (a preference). v2 names each, records only what the JD
-- says, and must quote it verbatim (pipeline/jd_extraction.py,
-- docs/jd-extraction-models.md).
--
-- The old values stay valid: every row records the prompt version that wrote
-- it (invariant #5), and a v1 row's `local_only` is still what v1 said. The
-- page reads each posting's newest extraction, so a v2 row supersedes the v1
-- row beside it without touching it.

BEGIN;

ALTER TABLE extractions DROP CONSTRAINT extractions_visa_signal_check;
ALTER TABLE extractions ADD CONSTRAINT extractions_visa_signal_check
    CHECK (visa_signal IN ('sponsors', 'local_only', 'unclear',
                           'citizens_pr_only', 'no_sponsorship', 'in_country',
                           'locals_preferred'));

COMMIT;
