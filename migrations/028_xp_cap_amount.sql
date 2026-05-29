-- 028_xp_cap_amount.sql
--
-- Chronicle-wide XP cap *amount*, so a shared deployment can set its own cap
-- instead of the hardcoded 350. Pairs with xp_cap_enabled (migration 027):
-- when the cap is on, claim approval + the "X / cap" display + the near-cap
-- dashboard tile all use this value. Default 350 = NYbN's house rule, so
-- existing chronicles are unchanged.
--
-- (The legacy per-character characters.xp_cap column is left in place but is
-- no longer the source of truth for enforcement/display.)

ALTER TABLE chronicle_settings ADD COLUMN xp_cap_amount INTEGER NOT NULL DEFAULT 350;
