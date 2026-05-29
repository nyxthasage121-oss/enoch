-- 015_period_schedules.sql
-- Saved schedule templates for batch-generating XP periods. Lets staff
-- define a chronicle's cadence once (e.g. NYbN biweekly Saturdays at
-- 20:00 UTC) and then stamp out the next N periods in one click.

CREATE TABLE IF NOT EXISTS period_schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    -- Label template with optional {n} (counter) or {date} (YYYY-MM-DD)
    -- placeholders. Falls back to "Night {n}" when blank.
    label_pattern   TEXT    NOT NULL DEFAULT 'Night {n}',
    period_type     TEXT    NOT NULL DEFAULT 'night',
    phase           TEXT    NOT NULL DEFAULT 'full',
    -- Cadence in days — 7 for weekly, 14 for biweekly, 28 for monthly.
    cadence_days    INTEGER NOT NULL DEFAULT 14,
    -- Anchor: first occurrence used as a reference when no periods
    -- generated yet. ISO datetime in UTC ("YYYY-MM-DDTHH:MM:SSZ").
    anchor_at       TEXT    NOT NULL,
    -- How long the window stays open, in hours (e.g. 48 = Fri evening
    -- through Sun evening).
    duration_hours  INTEGER NOT NULL DEFAULT 48,
    -- Counter — increments every time the schedule stamps a period, so
    -- {n} in the label_pattern matches "Night 1", "Night 2", etc.
    next_n          INTEGER NOT NULL DEFAULT 1,
    active          INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
