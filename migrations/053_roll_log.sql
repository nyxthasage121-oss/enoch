-- 053_roll_log.sql
-- Dice-roll history + stats (Inconnu B4). Every web/bot roll is recorded here so
-- the app can show full history + an outcome breakdown, and the bot can show a
-- player their last few rolls.
CREATE TABLE roll_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL DEFAULT 'roll',   -- roll | reroll | hunt | ...
    pool         INTEGER NOT NULL DEFAULT 0,
    hunger       INTEGER NOT NULL DEFAULT 0,
    difficulty   INTEGER NOT NULL DEFAULT 0,
    successes    INTEGER NOT NULL DEFAULT 0,
    outcome      TEXT    NOT NULL DEFAULT '',       -- success | critical | messy_critical | failure | total_failure | bestial_failure
    label        TEXT,                              -- e.g. "Strength 4 + Brawl 3 = 7d"
    dice         TEXT,                              -- rolled faces, comma-joined
    source       TEXT    NOT NULL DEFAULT 'web',    -- web | bot
    created_at   TEXT    NOT NULL
);
CREATE INDEX idx_roll_log_char ON roll_log (character_id, id DESC);
