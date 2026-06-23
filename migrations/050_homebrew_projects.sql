-- 050_homebrew_projects.sql
-- The Homebrew project engine (project_mode = 'homebrew'). A staff-set single
-- goal DC cumulative extended test, with two new mechanics vs NYbN:
--   * an OPTIONAL launch roll (chronicle-wide homebrew_launch_roll) — a roll to
--     open the project before the test begins;
--   * a messy crit / bestial failure PAUSES the project + flags the ST (instead
--     of NYbN's auto-bump), tracked by projects.paused.
-- The goal DC reuses the existing single-target counter (target_successes /
-- progress_successes); no stages.
ALTER TABLE chronicle_settings ADD COLUMN homebrew_launch_roll INTEGER NOT NULL DEFAULT 0;

-- Paused = a messy/bestial result is awaiting ST review; no rolls until cleared.
ALTER TABLE projects ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;
-- Launched = the extended test has opened. Defaults 1 so existing (NYbN) projects
-- and homebrew projects without a launch roll are immediately rollable; homebrew
-- projects that require a launch roll are set to 0 at approval.
ALTER TABLE projects ADD COLUMN launched INTEGER NOT NULL DEFAULT 1;
