-- 043_project_mode.sql
-- Chronicle-wide project ruleset toggle. Lets a chronicle pick how downtime
-- Projects work — or turn them off entirely — so the NYbN house rules can be
-- swapped for RAW/Homebrew or stripped. See docs/NYBN_DOWNTIME_PROJECTS.md.
--   nybn     — the current multi-stage extended-test engine (default)
--   homebrew — Launch roll + cumulative test to a staff-set goal DC (to build)
--   raw      — V5 Appendix II Project Die model (parked)
--   off      — Projects disabled / hidden

ALTER TABLE chronicle_settings ADD COLUMN project_mode TEXT NOT NULL DEFAULT 'nybn';
