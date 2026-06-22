-- 041_coterie_projects.sql
-- Phase D of the NYbN downtime ruleset: coterie projects. A project can be owned
-- by a coterie (coterie_id set) instead of an individual character. character_id
-- stays the proposer (any member may propose). Any coterie member may then roll
-- it via /project roll; each roll spends from the ROLLING member's own
-- per-character timeskip budget (no separate coterie pool) and successes
-- accumulate cumulatively across members on the shared stage list. Coterie stage
-- DCs use the elevated preset (30 / 45 / 60). See docs/NYBN_DOWNTIME_PROJECTS.md.
--
-- NULL coterie_id == an individual project (unchanged). Non-null == a coterie
-- project owned by that coterie.

ALTER TABLE projects ADD COLUMN coterie_id INTEGER REFERENCES coteries(id);

CREATE INDEX IF NOT EXISTS idx_projects_coterie ON projects(coterie_id);
