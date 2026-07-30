-- 012_engaged_event.sql — a new manual-only event type: `engaged`.
--
-- `viewed` is specifically the passive, auto-detected "your application was
-- viewed" email signal (matcher.py's _event_type(), keyed off
-- extraction.status_detail == 'viewed'). A recruiter or employer reaching out
-- directly — a call, a WhatsApp message, follow-up screening questions — with
-- no concrete next step yet is a different, stronger signal that deserves its
-- own bucket rather than being folded into that passive one or into the
-- generic, non-status-driving `note`. Manual-only for now, same as
-- follow_up_sent/note: no email-classification path produces it.
--
-- Precedence renumbered with gaps (multiples of 10) so `engaged` can sit
-- strictly between `viewed` and `interview_invite` without disturbing the
-- relative order of anything else; the gaps also leave room for a future
-- insertion without a third renumbering.

BEGIN;

ALTER TABLE events DROP CONSTRAINT events_type_check;
ALTER TABLE events ADD CONSTRAINT events_type_check
    CHECK (type IN
        ('interested','applied','confirmation','viewed','engaged',
         'rejected','interview_invite','recruiter_outreach',
         'follow_up_sent','offer','withdrawn','note'));

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
            ORDER BY e.occurred_at DESC,
                     CASE e.type
                         WHEN 'offer'            THEN 70
                         WHEN 'withdrawn'        THEN 60
                         WHEN 'rejected'         THEN 60
                         WHEN 'interview_invite' THEN 50
                         WHEN 'engaged'          THEN 45
                         WHEN 'viewed'           THEN 40
                         WHEN 'confirmation'     THEN 30
                         WHEN 'applied'          THEN 30
                         ELSE 10
                     END DESC
            LIMIT 1),
           'interested') AS status
FROM applications a;

-- CREATE OR REPLACE VIEW should preserve both of these, but re-assert them
-- explicitly rather than trust it silently — invariant #6: any view bypassing
-- security_invoker bypasses RLS for every caller.
ALTER VIEW application_status SET (security_invoker = true);
GRANT SELECT ON application_status TO tracker_app;

COMMIT;
