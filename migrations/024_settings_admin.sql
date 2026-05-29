-- 024_settings_admin.sql
--
-- Settings-admin gate, modeled on MCbN's SETTINGS_ADMIN_DISCORD_IDS
-- pattern. Decouples "can edit XP rules / tier budgets / chronicle-
-- wide config" from general staff status, so an over-eager ST can't
-- accidentally break the chronicle by toggling something they don't
-- fully understand.
--
-- Three-tier resolution (high priority wins):
--   1. ENOCH_SETTINGS_ADMIN_IDS env var (comma-separated discord_ids)
--      — for emergency access without DB writes.
--   2. player_profiles.settings_admin = 1 (this column)
--      — normal grants, manageable via the admin UI.
--   3. Otherwise: 403.
--
-- Backwards compat: every existing lead_st gets settings_admin=1
-- automatically so the current staff isn't locked out post-migration.

ALTER TABLE player_profiles ADD COLUMN settings_admin INTEGER NOT NULL DEFAULT 0;

UPDATE player_profiles SET settings_admin = 1 WHERE staff_role = 'lead_st';
