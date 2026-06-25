-- 058_backups.sql
-- Automated off-site backups. The hourly sweep posts a daily gzipped JSON
-- snapshot of the whole chronicle to a Discord webhook (so the data lives
-- somewhere other than the single Turso DB). `backup_webhook_url` is set by
-- staff in Admin; `last_backup_at` records the last successful post so the
-- sweep only fires ~once a day. Backups are off when the webhook is blank.
ALTER TABLE chronicle_settings ADD COLUMN backup_webhook_url TEXT;
ALTER TABLE chronicle_settings ADD COLUMN last_backup_at TEXT;
