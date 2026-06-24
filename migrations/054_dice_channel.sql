-- 054_dice_channel.sql
-- Per-chronicle Discord channel for web→Discord roll posting. When set, the web
-- Roll tab offers a "Post to Discord" opt-in: the result is enqueued to the
-- bot_outbox and Irad posts an embed to this channel. Stored as TEXT — Discord
-- channel IDs are 64-bit snowflakes. NULL/blank = feature off (no checkbox).
ALTER TABLE chronicle_settings ADD COLUMN dice_channel_id TEXT;
