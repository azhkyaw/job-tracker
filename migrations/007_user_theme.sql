-- 007_user_theme.sql — per-user colour theme for the web UI. NULL means
-- "auto": follow the browser/OS prefers-color-scheme. 'light' or 'dark' pin it
-- explicitly and win over the OS in both directions.
-- Display-only, like 006's timezone — nothing in the data model, matching or
-- worker logic reads this column; it only sets data-theme on <html>.

BEGIN;

ALTER TABLE users ADD COLUMN theme text CHECK (theme IN ('light','dark'));

COMMIT;
