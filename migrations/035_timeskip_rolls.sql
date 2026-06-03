-- 035_timeskip_rolls.sql
-- Per-character project-roll budget per timeskip. NYbN gives each character a
-- fixed number of project rolls per Time skip (default 8), shared across all of
-- their projects (and, later, other downtime actions). The cap is a chronicle
-- setting; usage is tracked per character per active play period.

ALTER TABLE chronicle_settings ADD COLUMN rolls_per_timeskip INTEGER NOT NULL DEFAULT 8;

CREATE TABLE IF NOT EXISTS timeskip_roll_usage (
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    period_id    INTEGER NOT NULL REFERENCES play_periods(id) ON DELETE CASCADE,
    rolls_used   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (character_id, period_id)
);
