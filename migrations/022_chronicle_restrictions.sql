-- 022_chronicle_restrictions.sql
--
-- Generic component-restriction table, modeled on MCbN's cc_restrictions.
-- Replaces the ad-hoc chronicle_settings.unlocked_predator_types JSON
-- column with a discriminated-by-type rowset that can extend to
-- loresheets, merits, backgrounds, disciplines-at-chargen, etc. without
-- another schema change.
--
-- Two modes:
--   'banned'   — a normally-allowed component is forbidden this chronicle.
--   'unlocked' — a default-restricted component is allowed this chronicle.
--
-- The set of default-restricted components lives in Python (e.g.
-- V5_RESTRICTED_PREDATOR_TYPES) so the table can stay shape-agnostic.

CREATE TABLE IF NOT EXISTS chronicle_restrictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component_type  TEXT NOT NULL,
    component_id    TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('banned', 'unlocked')),
    reason          TEXT,
    updated_by      TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(component_type, component_id, mode)
);

CREATE INDEX IF NOT EXISTS idx_chr_restrictions_type
    ON chronicle_restrictions(component_type);

-- Backfill: convert chronicle_settings.unlocked_predator_types (a JSON
-- list of names) into chronicle_restrictions rows with mode='unlocked'.
-- Uses json_each (available in SQLite 3.38+ and libsql). If json_valid
-- fails on the JSON, the row is skipped — better than aborting the whole
-- migration on a single malformed row.
INSERT OR IGNORE INTO chronicle_restrictions
    (component_type, component_id, mode, reason, updated_by, updated_at)
SELECT 'predator_type',
       je.value,
       'unlocked',
       'migrated from chronicle_settings.unlocked_predator_types',
       'system',
       datetime('now')
FROM chronicle_settings, json_each(chronicle_settings.unlocked_predator_types) je
WHERE chronicle_settings.unlocked_predator_types IS NOT NULL
  AND json_valid(chronicle_settings.unlocked_predator_types);
