-- 048_familiars.sql
-- Familiars (Animalism • "Bond Famulus"). A GLOBAL catalog of animal stat blocks
-- — the 7 V5 standards (seeded below) plus staff-added custom animals — shown to
-- players for reference. Plus per-character BONDS: a vampire with Animalism • can
-- bond a famulus (a catalog animal, given a pet name), shown on their sheet.
-- Stat shape is the simplified V5 animal block: Physical/Social/Mental dice
-- pools, Health/Willpower, named Exceptional pools (JSON), and an optional Special.
CREATE TABLE IF NOT EXISTS familiars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT,
    physical    INTEGER NOT NULL DEFAULT 1,
    social      INTEGER NOT NULL DEFAULT 1,
    mental      INTEGER NOT NULL DEFAULT 1,
    health      INTEGER NOT NULL DEFAULT 1,
    willpower   INTEGER NOT NULL DEFAULT 1,
    exceptional TEXT    NOT NULL DEFAULT '{}',   -- JSON {pool_name: rating}
    special     TEXT,
    is_standard INTEGER NOT NULL DEFAULT 0,      -- 1 = V5 standard (not editable/deletable)
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS character_familiars (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    familiar_id  INTEGER REFERENCES familiars(id) ON DELETE SET NULL,
    animal_name  TEXT    NOT NULL DEFAULT '',     -- denormalized catalog name (survives catalog edits)
    name         TEXT    NOT NULL,                -- the famulus's given name
    notes        TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_character_familiars_char ON character_familiars(character_id);

-- ── Seed the 7 V5 standard animals (Bond Famulus reference blocks) ──
INSERT INTO familiars (name, description, physical, social, mental, health, willpower, exceptional, special, is_standard, sort_order, created_by) VALUES
  ('Bat (Large)', 'Far from a predator, but benefits hugely from an impressive sonar ability.', 3, 1, 1, 2, 1, '{"Awareness":7,"Stealth":5}', NULL, 1, 1, 'system'),
  ('Bear', 'Impressive in size, lethality, and speed — a surprisingly dextrous creature capable of ripping flesh to ribbons.', 7, 1, 1, 8, 3, '{"Awareness":3,"Intimidation":6}', 'Add +2 to damage done by bear attacks.', 1, 2, 'system'),
  ('Bird of Prey', 'Hawk, eagle, vulture, and owl are among the more likely to keep company with a vampire.', 4, 1, 1, 3, 2, '{"Awareness":6,"Brawl":5,"Stealth":6}', NULL, 1, 3, 'system'),
  ('Guard Dog', 'Vicious yet obedient hounds to guard properties or set loose on intruders.', 5, 1, 1, 5, 2, '{"Awareness":4,"Brawl":6,"Intimidation":4,"Stealth":4}', 'Add +1 to damage done by guard dog bites.', 1, 4, 'system'),
  ('Horse', 'For quick, unexpected escape or as a stock of copious, if unpleasant, blood.', 6, 1, 1, 7, 2, '{"Awareness":4}', 'Horses do +2 damage when trampling prone opponents.', 1, 5, 'system'),
  ('Rat', 'The Nosferatu''s favored creature — ideal messengers or spies via Animalism.', 3, 1, 1, 1, 1, '{"Awareness":5,"Brawl":4,"Stealth":7}', 'When utilizing a swarm, add 3 to Health and all physical-based rolls.', 1, 6, 'system'),
  ('Wolf', 'For the vampire who wants to make a statement. Wolves often respect vampires as pack alphas.', 6, 1, 1, 6, 3, '{"Awareness":3,"Intimidation":5,"Stealth":5}', 'Add +1 to damage done by wolf attacks.', 1, 7, 'system');
