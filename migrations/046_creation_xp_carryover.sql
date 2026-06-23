-- 046_creation_xp_carryover.sql
-- Leftover creation XP (starting pool minus what was spent at creation) carries
-- into the character's running xp_total at approval, but is tracked here so it
-- stays EXEMPT from the chronicle XP cap (NYbN: starting XP doesn't count toward
-- the cap). Only EARNED XP (xp_total - creation_xp) counts toward the cap.
ALTER TABLE characters ADD COLUMN creation_xp INTEGER NOT NULL DEFAULT 0;
