-- 006_user_timezone.sql — per-user display timezone (IANA name, e.g.
-- "Asia/Singapore"). NULL means "not set yet", displayed as UTC.
-- This only affects rendering (pipeline/web.py's dt/dtt Jinja filters) —
-- everything is still stored and compared in UTC everywhere else.

BEGIN;

ALTER TABLE users ADD COLUMN timezone text;

COMMIT;
