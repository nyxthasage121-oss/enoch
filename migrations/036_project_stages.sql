-- 036_project_stages.sql
-- Phase A of the NYbN downtime ruleset: multi-stage projects. A roll project
-- becomes an ordered list of stages, each with its own DC (success threshold);
-- rolls accumulate toward the current stage, completing it and spilling overflow
-- into the next. Crit/messy/bestial modulate the spill + flag staff penalties.
-- See docs/NYBN_DOWNTIME_PROJECTS.md.

ALTER TABLE projects ADD COLUMN stages_json   TEXT    NOT NULL DEFAULT '[]';
ALTER TABLE projects ADD COLUMN current_stage INTEGER NOT NULL DEFAULT 0;
