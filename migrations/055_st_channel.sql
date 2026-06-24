-- 055_st_channel.sql
-- Per-chronicle "ST tracker" Discord channel. When set, staff can push a
-- snapshot of the live vitals board (every active char's Hunger/Health/WP/
-- Humanity + XP + mechanical-state flags) to this channel via Irad — a
-- staff-facing dashboard in chat. Stored as TEXT (64-bit snowflake);
-- NULL/blank = no "Post to Discord" button on the Vitals page.
ALTER TABLE chronicle_settings ADD COLUMN st_channel_id TEXT;
