-- 003_period_closing_reminder.sql
-- Track when we sent the "closing soon" Discord reminder for each period
-- so we don't send it twice. NULL = not yet sent.

ALTER TABLE play_periods ADD COLUMN closing_reminder_sent_at TEXT;
