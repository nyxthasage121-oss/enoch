-- 032_max_chars_per_player.sql
--
-- Per-player character cap. NYbN allows 2 characters per player; other
-- chronicles can raise/lower it, or set 0 for unlimited. The cap counts a
-- player's active + pending (awaiting-approval) characters — drafts, retired,
-- and Final Death characters don't count. Enforced at character creation.

ALTER TABLE chronicle_settings ADD COLUMN max_chars_per_player INTEGER NOT NULL DEFAULT 2;
