-- 039_coterie_background_blanks.sql
-- Coterie-level Background Blanking. A coterie's donated backgrounds form a
-- shared pool; any member can blank dots of one for the night, making them
-- unavailable to the WHOLE coterie until the next play period — the same
-- period-keyed release as the per-character character_backgrounds feature. The
-- pool total per background is derived from the active 'donated' contributions;
-- this table only tracks the blanks.

CREATE TABLE IF NOT EXISTS coterie_background_blanks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id        INTEGER NOT NULL REFERENCES coteries(id) ON DELETE CASCADE,
    name              TEXT    NOT NULL,                  -- display name, e.g. "Resources"
    bg_key            TEXT    NOT NULL,                  -- normalized slug, dedupe identity
    blanked_dots      INTEGER NOT NULL DEFAULT 0,
    blanked_period_id INTEGER REFERENCES play_periods(id) ON DELETE SET NULL,
    blanked_by        INTEGER REFERENCES characters(id) ON DELETE SET NULL,
    blanked_at        TEXT,
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(coterie_id, bg_key)
);

CREATE INDEX IF NOT EXISTS idx_coterie_bg_blanks_coterie ON coterie_background_blanks(coterie_id);
CREATE INDEX IF NOT EXISTS idx_coterie_bg_blanks_period  ON coterie_background_blanks(blanked_period_id);
