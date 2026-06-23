-- 049_downtime_actions.sql
-- A log of "downtime actions" a character spends their per-timeskip roll budget
-- on (migration 035). Hunting is the first: a player spends one project roll to
-- hunt during a Time skip. Kept generic (a `kind` + free-text note) so future
-- downtime actions (Willpower recovery, resonance cultivation, …) reuse it.
CREATE TABLE IF NOT EXISTS downtime_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    period_id    INTEGER REFERENCES play_periods(id) ON DELETE SET NULL,
    kind         TEXT    NOT NULL,            -- 'hunt' | (future: 'wp_recovery', …)
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_downtime_actions_char_period
    ON downtime_actions(character_id, period_id);
