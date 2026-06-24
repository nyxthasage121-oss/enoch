-- 052_resonance_mode.sql
-- Chronicle-wide Resonance table mode for the dice roller's Resonance generator
-- (core/resonance.py): standard | tattered_facade | add_empty.
--   * tattered_facade — swaps in the alternate Discipline associations from the
--     *Tattered Facade* supplement (same humor roll, different Disciplines).
--   * add_empty       — adds a ~1-in-6 chance of an Empty resonance.
ALTER TABLE chronicle_settings ADD COLUMN resonance_mode TEXT NOT NULL DEFAULT 'standard';
