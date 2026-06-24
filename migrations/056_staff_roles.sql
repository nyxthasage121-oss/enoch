-- 056_staff_roles.sql
-- In-app staff-access role picker. JSON array of Discord role IDs (as strings)
-- that grant staff-dashboard access, chosen from the admin UI instead of the
-- env var. UNIONed with the STAFF_ROLE_IDS env at login, so the env stays a
-- deploy-time backstop and a bad in-app edit can't lock everyone out.
ALTER TABLE chronicle_settings ADD COLUMN staff_role_ids TEXT;
