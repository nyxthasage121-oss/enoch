-- 051_dice_roller_toggle.sql
-- Chronicle-wide toggle for the web dice roller — the "Roll" tab on the
-- character page (the shared V5 engine in core/dice.py). Defaults ON: the
-- roller ships enabled, but a chronicle can remove the tab entirely.
ALTER TABLE chronicle_settings ADD COLUMN dice_roller_enabled INTEGER NOT NULL DEFAULT 1;
