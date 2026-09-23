-- 016: emails.sent_by_user, and the four classifications a SENT email can take.
--
-- Mail ingest reads Gmail's All Mail (gmail_imap._find_all_mail, for parity
-- with the API's messages.list), and All Mail holds what the user sends as
-- well as what they receive. Nothing recorded which was which, so the user's
-- own mail went through a classifier and a matcher written for mail FROM an
-- employer. Measured 24 Sep 2026: 26 of 1,119 stored emails were the user's
-- own, and they had produced 28 events — 11 interview_invite (a reply
-- confirming an interview slot, filed as the employer inviting them), 13 notes
-- (among them a follow-up chasing a silent employer, which is what the
-- follow_up_sent type exists for, and which had been filed exactly once by
-- hand across the whole search), 3 applied, 1 confirmation — and two resume
-- emails, i.e. two applications, classified not_job_related with their bodies
-- purged.
--
-- The fact is Gmail's own SENT system label, not the From address: labels are
-- applied by Gmail when the account sends, so aliases are covered with no list
-- of the user's addresses to maintain, and a message the user FORWARDED into
-- this mailbox from another of their own accounts is correctly a received one
-- (the real case: a confirmation forwarded from a second address, whose
-- content is the employer's and which a From-address rule would have
-- misfiled). Verified on the real mailbox before this was written: every one
-- of the 26 own-address messages but that forward carries \Sent, and nothing
-- else does.
--
-- The classification of a sent email says what the USER did, which is a
-- different vocabulary from what an employer did — hence its own values
-- rather than a reuse of 'confirmation'/'interview_invite' (matcher.EVENT_TYPE
-- maps each to its event; email_classifier.SENT_TYPES is the list).
-- Every existing row is received mail by default; the 26 are set by the
-- 24 Sep backfill from the labels themselves.

BEGIN;

ALTER TABLE emails ADD COLUMN sent_by_user boolean NOT NULL DEFAULT false;

ALTER TABLE emails DROP CONSTRAINT emails_classification_check;
ALTER TABLE emails ADD CONSTRAINT emails_classification_check
    CHECK (classification IN
        ('confirmation','rejection','interview_invite','recruiter_outreach',
         'status_update','other','not_job_related',
         'sent_application','sent_follow_up','sent_reply','sent_withdrawal'));

COMMIT;
