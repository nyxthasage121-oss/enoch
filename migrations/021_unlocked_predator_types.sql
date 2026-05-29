-- 021_unlocked_predator_types.sql
--
-- Per Steward direction (2026-05): some predator types (Blood Leech and
-- Tithe Collector) are usually banned in V5 chronicles because they
-- short-circuit the standard feeding economy. Default the wizard to
-- hide them, but let staff opt-in per chronicle.
--
-- Stored as a JSON list of predator type names that have been unlocked
-- (e.g. '["Blood Leech"]'). The route's `_available_predator_types()`
-- filter consults this against `V5_RESTRICTED_PREDATOR_TYPES`.

ALTER TABLE chronicle_settings ADD COLUMN unlocked_predator_types TEXT;
