-- 018_profile_lock_and_ingrained_disc.sql
-- Two parity additions ported from nybn-xp-tracker:
--
-- profile_locked: when 1, the owning player can no longer edit
-- the IC profile (blurb, pronouns, ages, backstory, image). Staff
-- flip this from the character detail page once they're satisfied
-- with an approved profile, to prevent drift later.
--
-- ingrained_discipline: which discipline carries the Ingrained Flaw
-- (e.g. "Auspex", "Dominate"). NULL when has_ingrained_flaw is 0
-- or when staff hasn't recorded which discipline yet. Lets staff
-- grant the flaw post-approval and track its mechanical effect.

ALTER TABLE characters ADD COLUMN profile_locked       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN ingrained_discipline TEXT;
