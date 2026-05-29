-- 010_ancilla_in_memoriam.sql
-- Phase 4: Ancilla character tier + In Memoriam ("Oceans of Time") path.
--
-- character_tier:
--   'neonate' (default) — fresh-Embraced, standard chargen
--   'ancilla'           — mid-tier vampire; can pick Standard (+35 XP) or In Memoriam
--   'elder'             — long-lived vampire (chronicle-specific perks; stored but not yet rule-wired)
--
-- ancilla_mode:
--   'standard'     — +35 XP boost over neonate baseline
--   'in_memoriam'  — Oceans of Time era builder
--   NULL           — n/a (character is not an ancilla)
--
-- im_generation: '12th' / '11th-10th' / '9th-8th'  → sets Blood Potency
-- im_discipline_spread: 'focused' (3+1+1) / 'strategic' (2+2+1+1)
-- in_memoriam: full JSON blob {embrace_age, eras: [{type, gambit_taken, gambit_roll, ...}]}
--              plus computed totals total_xp / humanity_loss

ALTER TABLE characters ADD COLUMN character_tier        TEXT NOT NULL DEFAULT 'neonate';
ALTER TABLE characters ADD COLUMN ancilla_mode          TEXT;
ALTER TABLE characters ADD COLUMN im_generation         TEXT;
ALTER TABLE characters ADD COLUMN im_discipline_spread  TEXT;
ALTER TABLE characters ADD COLUMN in_memoriam           TEXT NOT NULL DEFAULT '{}';
