-- 045_coterie_member_cap.sql
-- Per-chronicle coterie member cap. Was a hardcoded config constant
-- (COTERIE_MAX_MEMBERS); make it a chronicle setting so each server can set how
-- many characters may belong to one coterie. Default 6 matches the old constant.
ALTER TABLE chronicle_settings ADD COLUMN coterie_max_members INTEGER NOT NULL DEFAULT 6;
