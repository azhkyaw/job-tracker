-- 019_offer_is_a_round.sql — an offer is a round, not a close.
--
-- Real case, 7 Oct 2026: an agency's recruiter rang on 21 Sep with the
-- client's offer — the seat had moved to another city, relocation required.
-- The user asked on the 22nd for the original city to be reconsidered and
-- heard nothing more. Nothing in the model could say so. `offer` ranked
-- ABOVE both closes (70, against 60 for `rejected` and `withdrawn`), and
-- since migration 013 the highest precedence ever recorded wins, so once an
-- offer was filed the status read "offer" for good: a declined offer, a
-- rescinded one and an offer gone quiet in negotiation were all a success —
-- green, closed, counted, and impossible to close (web.close_approach
-- refuses a thread holding a terminal event). The search's first offer was
-- the first time anyone noticed.
--
-- Now `offer` ranks between `interview_invite` and the closes: the thread it
-- opens is still open — yours to accept or decline (`withdrawn` with
-- `payload.closed = 'declined'` and a `why`), theirs to withdraw
-- (`rejected`, with a reason) — and until one of those it waits like any
-- round, reaching /follow-ups after REMINDER_DAYS of silence. 55 is the gap
-- the gapped values left between 50 and 60; nothing else moves. An offer
-- that was made still counts as one (analytics.summary's `offered` is an
-- EXISTS over the events), whatever came after it.
--
-- A pure view, so every record reads right with no backfill; on the dev DB
-- that was one application.

BEGIN;

CREATE OR REPLACE VIEW application_status AS
SELECT a.id AS application_id,
       a.user_id,
       a.job_id,
       COALESCE(
           (SELECT e.type
            FROM events e
            WHERE e.application_id = a.id
              AND e.type IN ('interested','applied','confirmation','viewed',
                             'engaged','interview_invite','rejected','offer',
                             'withdrawn')
            ORDER BY CASE e.type
                         WHEN 'withdrawn'        THEN 60
                         WHEN 'rejected'         THEN 60
                         WHEN 'offer'            THEN 55
                         WHEN 'interview_invite' THEN 50
                         WHEN 'engaged'          THEN 45
                         WHEN 'viewed'           THEN 40
                         WHEN 'confirmation'     THEN 30
                         WHEN 'applied'          THEN 30
                         ELSE 10
                     END DESC,
                     e.occurred_at DESC
            LIMIT 1),
           'interested') AS status
FROM applications a;

-- Re-asserted as 013 does — invariant #6: a view without security_invoker
-- bypasses RLS for every caller.
ALTER VIEW application_status SET (security_invoker = true);
GRANT SELECT ON application_status TO tracker_app;

COMMIT;
