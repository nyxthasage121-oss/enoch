-- 033_character_backgrounds.sql
-- Background Blanking: per-character tracked V5 backgrounds with a one-night
-- blank/release cycle (ported from the MCbN tracker).
--
-- The source repo keyed the release off an integer night ordinal (release =
-- night + 1). Enoch has no night ordinal — it has one active play period at a
-- time — so a blank is instead tied to the period it was made in
-- (blanked_period_id) and becomes due once a *different* period is active (the
-- next night has opened). Release is driven web-side from set_period_active and
-- the hourly lifespan sweep, which enqueue a `background_released` bot event.

CREATE TABLE IF NOT EXISTS character_backgrounds (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id      INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    name              TEXT    NOT NULL,                 -- display name, e.g. "Allies"
    bg_key            TEXT    NOT NULL,                 -- normalized slug, per-character dedupe identity
    dots              INTEGER NOT NULL DEFAULT 0,       -- total dots in the background
    blanked_dots      INTEGER NOT NULL DEFAULT 0,       -- dots currently blanked (unavailable)
    blanked_period_id INTEGER REFERENCES play_periods(id) ON DELETE SET NULL,
    blanked_at        TEXT,                             -- ISO-8601 of the most recent blank
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_by        TEXT    NOT NULL DEFAULT '',
    UNIQUE(character_id, bg_key)
);

CREATE INDEX IF NOT EXISTS idx_character_backgrounds_character
    ON character_backgrounds(character_id);
CREATE INDEX IF NOT EXISTS idx_character_backgrounds_period
    ON character_backgrounds(blanked_period_id);
