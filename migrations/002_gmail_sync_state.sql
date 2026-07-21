-- 002_gmail_sync_state.sql — cursor storage for incremental Gmail sync (§6.2)
BEGIN;

CREATE TABLE gmail_sync_state (
    user_id         uuid PRIMARY KEY REFERENCES users(id),
    history_id      text,
    last_synced_at  timestamptz
);

COMMIT;
