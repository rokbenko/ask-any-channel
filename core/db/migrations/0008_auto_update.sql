-- auto_update defaults off: polite to YouTube by default, an operator opts a channel in.
-- last_checked_at is "when we last enqueued an incremental-update check" — NULL means never.
ALTER TABLE channels ADD COLUMN auto_update BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE channels ADD COLUMN last_checked_at TIMESTAMPTZ;
