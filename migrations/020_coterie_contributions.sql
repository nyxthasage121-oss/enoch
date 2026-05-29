-- 020_coterie_contributions.sql
--
-- The contributions model — per Steward direction (2026-05): track every
-- dot that lands on a coterie sheet by who contributed it and why, so we
-- can suspend a contributor's dots when they go inactive (and reactivate
-- when they come back). Replaces the implicit "flat columns on coteries"
-- model for accounting purposes.
--
-- The flat `coteries.chasse/lien/portillon` columns stay as a denormalized
-- cache updated by `_recompute_coterie_ratings()` after every contribution
-- mutation — anything that just reads the rating (UI, sweeps, dice bot)
-- keeps working without joining this table.
--
-- New spend categories use:
--   contribution_type = 'paid_xp'    — one member paid personal XP
--   contribution_type = 'donated'    — member transferred sheet trait
--   contribution_type = 'timeskip_advance' — C/L/P bump per period
--   contribution_type = 'creation_free' — 2-dot creation pool
--   contribution_type = 'flaw_bonus' — +4 dots from unanimous flaws
--   contribution_type = 'staff_grant' — backfill / direct staff action
--
-- Status lifecycle:
--   'active'    → counts toward effective rating
--   'suspended' → set when contributing member is flagged inactive
--   'removed'   → contributor left coterie or staff revoked

CREATE TABLE IF NOT EXISTS coterie_contributions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id        INTEGER NOT NULL REFERENCES coteries(id) ON DELETE CASCADE,
    -- character_id is NULL for staff-granted dots that don't tie to a
    -- specific player (e.g. backfilled legacy ratings, NPC seedings).
    character_id      INTEGER REFERENCES characters(id) ON DELETE SET NULL,
    contribution_type TEXT    NOT NULL,
    target_kind       TEXT    NOT NULL,
    target_name       TEXT,   -- null for chasse/lien/portillon
    dots              INTEGER NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'active',
    xp_paid           INTEGER NOT NULL DEFAULT 0,
    period_id         INTEGER REFERENCES play_periods(id),
    -- ties to the spend pipeline row that funded this contribution
    -- (null for direct staff grants / creation pool / flaw bonus).
    spend_id          INTEGER REFERENCES coterie_spends(id) ON DELETE SET NULL,
    note              TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_coterie_contributions_co
    ON coterie_contributions(coterie_id, status);
CREATE INDEX IF NOT EXISTS idx_coterie_contributions_char
    ON coterie_contributions(character_id);
CREATE INDEX IF NOT EXISTS idx_coterie_contributions_target
    ON coterie_contributions(coterie_id, target_kind, target_name);


-- ── Backfill from existing flat columns ──────────────────────────────
-- One row per existing dot — staff-granted, NULL character_id, active.
-- This makes the effective_rating helper return the right values from
-- day 1 without anyone having to manually re-enter contributions.

INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, note)
SELECT id, NULL, 'staff_grant', 'chasse', NULL, chasse, 'active',
       'Backfilled from coteries.chasse during migration 020'
FROM coteries WHERE chasse > 0;

INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, note)
SELECT id, NULL, 'staff_grant', 'lien', NULL, lien, 'active',
       'Backfilled from coteries.lien during migration 020'
FROM coteries WHERE lien > 0;

INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, note)
SELECT id, NULL, 'staff_grant', 'portillon', NULL, portillon, 'active',
       'Backfilled from coteries.portillon during migration 020'
FROM coteries WHERE portillon > 0;


-- Backfill from coterie_merits — preserve the per-character attribution
-- that's already encoded there. merit_type maps to contribution_type:
--   'creation' → 'creation_free'
--   'donated'  → 'donated'
--   'purchased'→ 'paid_xp'   (NB: xp_paid stays 0 — we don't know the
--                            historical cost; future flows will populate it)
INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, note, created_at)
SELECT
    coterie_id,
    character_id,
    CASE merit_type
        WHEN 'creation' THEN 'creation_free'
        WHEN 'donated'  THEN 'donated'
        ELSE                 'paid_xp'
    END,
    'merit',
    merit_name,
    dots,
    'active',
    'Backfilled from coterie_merits during migration 020',
    COALESCE(created_at, datetime('now'))
FROM coterie_merits;


-- Backfill from coterie_flaws — coterie flaws aren't per-member in the
-- existing schema, so we record them with NULL character_id.
INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, note, created_at)
SELECT
    coterie_id,
    NULL,
    'flaw_bonus',
    'flaw',
    flaw_name,
    dots,
    'active',
    'Backfilled from coterie_flaws during migration 020',
    COALESCE(created_at, datetime('now'))
FROM coterie_flaws;


-- ── Extend coterie_spends for single-funder flows ────────────────────
-- The legacy per-member equal-split (`contributions` JSON map) stays in
-- place for domain group-buys. For the new flows (timeskip C/L/P advance,
-- personal-XP merit, donation), one member funds the whole thing and we
-- need to remember who.

ALTER TABLE coterie_spends ADD COLUMN funded_by_character_id INTEGER REFERENCES characters(id);
-- contribution_type stored on the spend itself so approve_coterie_spend()
-- can decide which side-effect to apply (write contribution row, mutate
-- sheet, etc.) without re-deriving from spend_category.
ALTER TABLE coterie_spends ADD COLUMN contribution_type TEXT;
-- period_id pins a timeskip-gated spend to its period so the "already
-- advanced this period" soft warning can detect duplicates.
ALTER TABLE coterie_spends ADD COLUMN period_id INTEGER REFERENCES play_periods(id);
