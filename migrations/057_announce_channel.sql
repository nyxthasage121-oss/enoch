-- 057_announce_channel.sql
-- Move the chronicle announcement channel (period-closing reminders, etc.) from
-- the CHRONICLE_CHANNEL_ID env var into a DB setting, so staff can set it from
-- the web admin or the bot /settings command. The web resolves it and carries
-- the id in the period_closing_soon payload, so the bot no longer needs its own
-- env/config for it (closes the bot-config gap). Falls back to the env var when
-- this is blank. Stored as TEXT (64-bit snowflake).
ALTER TABLE chronicle_settings ADD COLUMN announce_channel_id TEXT;
