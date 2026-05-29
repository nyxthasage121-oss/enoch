-- 005_sheet_required_toggle.sql
-- Chronicle-wide setting: should the character creation wizard force
-- players through the full sheet (attributes, skills, disciplines,
-- merits/flaws, rituals, touchstones), or just collect the basics and
-- let staff approve from an offline source (PDF, Progeny etc)?
--
-- 1 = wizard requires the full sheet (default, fits public use)
-- 0 = skip steps 4-9; player fills the sheet later via the Sheet tab
--     (fits NYbN's "PDF-approved-elsewhere" workflow)

ALTER TABLE chronicle_settings
  ADD COLUMN require_sheet_on_create INTEGER NOT NULL DEFAULT 1;
