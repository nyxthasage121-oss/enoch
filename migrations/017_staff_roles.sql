-- 017_staff_roles.sql
-- Granular staff roles on top of the existing Discord-role-based gate.
-- The boolean staff flag (Discord role membership) decides whether a
-- player can SEE the staff dashboard at all; staff_role determines
-- WHAT they're allowed to do once inside.
--
-- Roles:
--   lead_st  — Lead ST: every permission, including role management
--   co_st    — Co-Storyteller: everything except role management + chronicle settings
--   reviewer — Reviewer: approve/reject claims + spends only
--   helper   — Helper: read-only on the dashboard
--
-- NULL means "no Enoch role assigned yet" — falls back to read-only when
-- the Discord role grants staff access.

ALTER TABLE player_profiles ADD COLUMN staff_role TEXT;
