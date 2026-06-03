-- 034_projects.sql
-- Downtime Projects: a character proposes an endeavour, staff approve it and
-- choose how it runs, the player works it over successive nights, and on
-- completion staff (or the structured config) grant the payoff.
--
-- Lifecycle (status):  proposed -> active -> complete   (or  proposed -> rejected)
-- Progress (set by staff at approval):
--   'staged'  — staff advance it manually, leaving notes each downtime
--   'roll'    — a V5 extended test; the player rolls once per play period via the
--               bot, accumulating successes toward target_successes
-- Payoff (set by staff at approval, applied web-side on completion):
--   'freeform'   — staff write a free-text outcome
--   'structured' — auto-grant reward_dots of (reward_category/reward_trait) and/or
--                  reward_xp onto the sheet
--
-- MVP scope: individual characters only (character_id is required, no coterie owner).

CREATE TABLE IF NOT EXISTS projects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id        INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    title               TEXT    NOT NULL,
    description         TEXT    NOT NULL DEFAULT '',
    status              TEXT    NOT NULL DEFAULT 'proposed',  -- proposed|active|complete|rejected
    progress_type       TEXT,                                 -- staged|roll (set on approval)
    payoff_type         TEXT,                                 -- freeform|structured (set on approval)

    -- Roll (extended-test) configuration + state
    roll_pool           TEXT    NOT NULL DEFAULT '',          -- e.g. "resolve + occult" or a flat number
    roll_difficulty     INTEGER NOT NULL DEFAULT 1,           -- per-roll difficulty for outcome flavour
    target_successes    INTEGER NOT NULL DEFAULT 0,           -- total successes to complete
    progress_successes  INTEGER NOT NULL DEFAULT 0,           -- accumulated so far
    last_roll_period_id INTEGER REFERENCES play_periods(id),  -- one roll per period

    -- Payoff configuration (structured) / outcome (freeform)
    reward_text         TEXT    NOT NULL DEFAULT '',          -- freeform outcome, or a note for structured
    reward_category     TEXT,                                 -- structured: a SPEND_CATEGORIES value
    reward_trait        TEXT,                                 -- structured: trait name
    reward_dots         INTEGER NOT NULL DEFAULT 0,           -- structured: dots to grant
    reward_xp           INTEGER NOT NULL DEFAULT 0,           -- structured: raw XP to grant
    payoff_applied      INTEGER NOT NULL DEFAULT 0,           -- 0|1

    log_json            TEXT    NOT NULL DEFAULT '[]',         -- timeline: notes, rolls, status changes
    proposed_by         TEXT    NOT NULL,                      -- discord_id of the proposing player
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_character ON projects(character_id);
CREATE INDEX IF NOT EXISTS idx_projects_status    ON projects(status);
