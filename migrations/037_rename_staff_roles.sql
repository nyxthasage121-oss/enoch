-- 037_rename_staff_roles.sql
-- Rename staff roles to match the Discord server's vocabulary:
--   Admin / Moderator / Storyteller / Helper.
--
-- Old → new (pre-launch; in practice only the dev/seed admin row exists):
--   lead_st  → admin        (full control incl. settings + role assignment)
--   co_st    → storyteller  (full XP + ST management, no settings/roles)
--   reviewer → storyteller  (the old Reviewer role is retired; nearest game-staff equivalent)
--   helper   → helper       (key unchanged; Helper now grants "approve spends only")
--
-- staff_role is a plain TEXT column with no CHECK constraint (see 017_staff_roles),
-- so a straight UPDATE suffices — no table rebuild needed. 'moderator' is a brand-new
-- role with no legacy rows; NULL (no role assigned) is left untouched.

UPDATE player_profiles SET staff_role = 'admin'       WHERE staff_role = 'lead_st';
UPDATE player_profiles SET staff_role = 'storyteller' WHERE staff_role IN ('co_st', 'reviewer');
