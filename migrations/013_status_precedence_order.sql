-- 013_status_precedence_order.sql — fix application_status: precedence must
-- be the PRIMARY sort key, occurred_at only the tiebreaker.
--
-- Real bug, caught on a live application (Tailspin Consulting, 2 Aug 2026):
-- interview_invite landed 2026-07-24 10:29:24, then a technical-assessment
-- receipt (classified `confirmation`, not status-driving in intent) landed
-- 2026-07-31 10:29:41. Because the view's ORDER BY was
-- `occurred_at DESC, <precedence CASE> DESC`, the later-but-lower-precedence
-- confirmation row sorted first and became the shown status — "interviewing"
-- silently regressed to "applied" a week later.
--
-- The gapped CASE values (multiples of 10, extended for `engaged` in
-- migration 012) exist specifically to rank event types against each other;
-- treating occurred_at as the primary key made that ranking a tiebreaker
-- only, which is backwards — it only ever mattered on an exact-instant tie
-- (same pattern the "testing a precedence tie" gotcha and the engaged-vs-
-- viewed / interview_invite-vs-engaged tests in test_web.py already probe).
-- Swapping the sort-key order keeps every one of those tie cases passing
-- (same-instant ties still resolve by the CASE) while fixing the real,
-- different-instant regression: the highest-precedence event ever recorded
-- now wins regardless of what happened after it, and occurred_at only
-- breaks ties between events of equal precedence.

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
                         WHEN 'offer'            THEN 70
                         WHEN 'withdrawn'        THEN 60
                         WHEN 'rejected'         THEN 60
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

-- CREATE OR REPLACE VIEW should preserve both of these, but re-assert them
-- explicitly rather than trust it silently — invariant #6: any view bypassing
-- security_invoker bypasses RLS for every caller.
ALTER VIEW application_status SET (security_invoker = true);
GRANT SELECT ON application_status TO tracker_app;

COMMIT;
