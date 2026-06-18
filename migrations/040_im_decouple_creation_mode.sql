-- 040_im_decouple_creation_mode.sql
-- Untangle In Memoriam from the single active_ruleset, and add a chargen-mode
-- switch so a chronicle can launch on open sheet-entry before turning on the
-- guided creator.
--
-- BEFORE: active_ruleset ∈ {standard, homebrew, in_memoriam}. Turning IM on
--   overwrote the chosen base ruleset and forced EVERY Ancilla through the Era
--   Builder — so "Standard + IM both, player chooses" was impossible.
-- AFTER:  active_ruleset ∈ {standard, homebrew} (the base budget rules) and
--   in_memoriam_enabled is an ORTHOGONAL flag. When on, Ancilla players may
--   CHOOSE the In Memoriam path; the wizard offers Standard vs In Memoriam.
--
-- creation_mode:
--   'guided' (default) — the wizard enforces the active standard (RAW / IM /
--                        Homebrew budgets + spreads).
--   'open'             — no enforcement; players just enter their sheet. Use at
--                        launch to drop in existing/imported sheets, then flip
--                        to 'guided' later.

ALTER TABLE chronicle_settings ADD COLUMN in_memoriam_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chronicle_settings ADD COLUMN creation_mode TEXT NOT NULL DEFAULT 'guided';

-- Carry forward any chronicle that ran IM as its active ruleset: keep the Era
-- Builder available (flag on) and fall back to the Standard base. The old
-- collapsed value lost which base ruleset was originally picked, so a
-- homebrew+IM chronicle must re-select Homebrew after upgrading.
UPDATE chronicle_settings SET in_memoriam_enabled = 1 WHERE active_ruleset = 'in_memoriam';
UPDATE chronicle_settings SET active_ruleset = 'standard' WHERE active_ruleset = 'in_memoriam';
