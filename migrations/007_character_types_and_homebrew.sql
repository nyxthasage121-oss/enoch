-- 007_character_types_and_homebrew.sql
-- Phase 1: character archetypes + chronicle-wide homebrew creation rules.
--
-- 1. Tag every character as a Kindred (default), Mortal, Ghoul, or Revenant.
--    Revenants also store a family name (free text — admin curates the
--    canonical list separately so the wizard can present a dropdown).
-- 2. Storyteller can flip homebrew rules on/off chronicle-wide and define
--    custom starting XP / merit / advantage budgets. When off, the wizard
--    uses V5 RAW defaults (75 XP for neonates; merit/advantage = 7/2).
-- 3. Storyteller can enable Revenants as a playable type and pin the
--    canonical family list (JSON array of {name, parent_clan}). The
--    wizard hides Revenant as a choice when the toggle is off.

ALTER TABLE characters ADD COLUMN character_type   TEXT NOT NULL DEFAULT 'kindred';
ALTER TABLE characters ADD COLUMN revenant_family  TEXT;
ALTER TABLE characters ADD COLUMN ghoul_regnant    TEXT;

ALTER TABLE chronicle_settings ADD COLUMN use_homebrew_rules        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chronicle_settings ADD COLUMN homebrew_starting_xp      INTEGER NOT NULL DEFAULT 75;
ALTER TABLE chronicle_settings ADD COLUMN homebrew_merit_budget     INTEGER NOT NULL DEFAULT 7;
ALTER TABLE chronicle_settings ADD COLUMN homebrew_advantage_budget INTEGER NOT NULL DEFAULT 2;
ALTER TABLE chronicle_settings ADD COLUMN revenants_enabled         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chronicle_settings ADD COLUMN revenant_families         TEXT    NOT NULL DEFAULT '[]';
