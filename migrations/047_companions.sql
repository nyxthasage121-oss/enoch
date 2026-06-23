-- 047_companions.sql
-- Retainers & Mawlas (issue #1): named, statted NPCs attached to a player
-- character. A companion RIDES its parent's approval (no separate review queue)
-- and is shown on the character profile. Retainers are built from the V5
-- "Mortals Templates" (Weak / Average / Gifted / Deadly), scaled by the Retainer
-- background rating (● Weak, ●● Average, ●●● Gifted); Mawlas are Kindred built
-- under the chronicle's active ruleset. Blanking ties in through the parent's
-- matching background dots (bg_key links the companion to its character_backgrounds
-- row so it can be blanked for the night like any other background).
CREATE TABLE IF NOT EXISTS companions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    kind                TEXT    NOT NULL,                 -- 'retainer' | 'mawla'
    name                TEXT    NOT NULL,
    dots                INTEGER NOT NULL DEFAULT 1,       -- background rating (retainer 1-3, mawla 1-5)
    template            TEXT,                             -- retainer: 'weak'|'average'|'gifted'|'deadly'
    is_ghoul            INTEGER NOT NULL DEFAULT 0,       -- retainer-as-ghoul (1 Discipline dot)
    clan                TEXT,                             -- mawla clan / ghoul domitor clan reference
    concept             TEXT,
    description         TEXT,
    sheet_json          TEXT    NOT NULL DEFAULT '{}',    -- stat block: attr_*, skill_*, disc_*, specialties[], merits[], flaws[]
    bg_key              TEXT,                             -- character_backgrounds.bg_key this maps to (blanking)
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_companions_parent ON companions(parent_character_id);
