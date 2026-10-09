-- 021_status_reads_rounds.sql — an invitation that is not a round does not
-- make a thread "interviewing".
--
-- Real case, 9 Oct 2026: an application whose only invitations were an
-- employer's automated screening questionnaire (3 questions, every applicant
-- gets one) and its reminder. The rounds had read it right since the day
-- before — trace.rounds leaves a round of a kind that is not one
-- (trace.NON_ROUND_KINDS) out, so the row carried no rounds tag and the
-- lists' interviews figure did not count it — but this view ranked events by
-- TYPE alone, so the status said "interviewing". The view could not know: the
-- kind (`payload.round_kind`), the mail's own role (`invite_role`, stage 4)
-- and your "not a round" (`round_is`) all arrived on 8-9 Oct as qualifiers
-- on the invitation event, after migration 019 last restated the view.
--
-- Now each event is ranked as what it COUNTS as, analytics.effective_type_sql
-- written out here (static SQL cannot import it; tests/test_web.py holds the
-- two, and insights.effective_type, equal on every event, and holds
-- "interviewing" to the threads trace.rounds finds a round on):
--   - an invitation of a non-round kind, on it or on any event of the same
--     round (analytics.same_round_sql: both mail-borne invitations naming
--     one day, or within trace.ROUND_SPAN_DAYS = 3 days of each other), is
--     part of applying and ranks as the confirmation: the status reads
--     "applied";
--   - an invitation excluded by its own line — a mail arranging or
--     cancelling an interview (invite_role), or a line you said is not a
--     round (round_is = 'none') — is a person writing about an interview,
--     and ranks as `engaged`;
--   - every other event ranks as its type, at 019's values.
-- The status column returns the effective type, so the questionnaire's
-- thread reads 'confirmation', which the app shows as "applied".
--
-- On the dev DB it moves one application (that one), and no other count:
-- 41 invitation lines read as interviews, 17 as a person writing, 5 as a
-- questionnaire. A pure view, so nothing is backfilled.
--
-- The literals below mirror trace.py: NON_ROUND_KINDS ('questionnaire'),
-- NON_ROUND_ROLES ('scheduling', 'cancellation'), NOT_A_ROUND ('none'),
-- ROUND_SPAN_DAYS (3), own_round (source 'manual'). Change one there and a
-- new migration restates this view; the suite fails until it does.

BEGIN;

CREATE OR REPLACE VIEW application_status AS
SELECT a.id AS application_id,
       a.user_id,
       a.job_id,
       COALESCE(
           (SELECT c.counts_as
            FROM events e
            CROSS JOIN LATERAL (
                SELECT CASE
                    WHEN e.type <> 'interview_invite' THEN e.type
                    WHEN EXISTS (
                        SELECT 1 FROM events k
                         WHERE k.application_id = e.application_id
                           AND k.payload->>'round_kind' IN ('questionnaire')
                           AND (k.id = e.id
                                OR (k.type = 'interview_invite'
                                    AND k.source <> 'manual' AND e.source <> 'manual'
                                    AND (COALESCE(k.payload->>'stated_date', '')
                                           = COALESCE(e.payload->>'stated_date', '?')
                                         OR k.occurred_at BETWEEN e.occurred_at - make_interval(days => 3)
                                                              AND e.occurred_at + make_interval(days => 3)))))
                        THEN 'confirmation'
                    WHEN COALESCE(e.payload->>'round_is', '') = 'none'
                      OR COALESCE(e.payload->>'invite_role', '') IN ('scheduling', 'cancellation')
                        THEN 'engaged'
                    ELSE e.type
                END AS counts_as
            ) c
            WHERE e.application_id = a.id
              AND e.type IN ('interested','applied','confirmation','viewed',
                             'engaged','interview_invite','rejected','offer',
                             'withdrawn')
            ORDER BY CASE c.counts_as
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

-- Re-asserted as 013 and 019 do — invariant #6: a view without
-- security_invoker bypasses RLS for every caller.
ALTER VIEW application_status SET (security_invoker = true);
GRANT SELECT ON application_status TO tracker_app;

COMMIT;
