-- 001_initial.sql — Enoch initial schema
-- Applies once; tracked in _migrations table (created by db.py run_migrations).

-- ── Chronicle settings (singleton row) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronicle_settings (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    server_start_date   TEXT    NOT NULL,          -- ISO-8601 date string
    xp_frequency        TEXT    NOT NULL DEFAULT 'weekly',  -- 'weekly' | 'biweekly'
    night_start_hour    INTEGER NOT NULL DEFAULT 18, -- local 24h hour dusk begins
    timeskip_interval   INTEGER NOT NULL DEFAULT 30, -- days between timeskips
    midnight_split      INTEGER NOT NULL DEFAULT 0,  -- 0|1 boolean
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Player profiles (Discord user cubby / channel tracking) ──────────────────
CREATE TABLE IF NOT EXISTS player_profiles (
    discord_id      TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    cubby_channel   TEXT,                           -- Discord channel ID for DMs
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Characters ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS characters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      TEXT    NOT NULL REFERENCES player_profiles(discord_id),
    name            TEXT    NOT NULL,
    clan            TEXT    NOT NULL,               -- canonical clan slug e.g. 'tremere'
    predator_type   TEXT,
    concept         TEXT,
    sire            TEXT,
    covenant        TEXT,
    -- Sheet snapshot fields (stored as JSON for flexibility)
    sheet_json      TEXT    NOT NULL DEFAULT '{}',  -- full sheet snapshot
    -- XP tracking
    xp_total        INTEGER NOT NULL DEFAULT 0,     -- lifetime earned
    xp_spent        INTEGER NOT NULL DEFAULT 0,     -- lifetime spent
    xp_cap          INTEGER NOT NULL DEFAULT 350,   -- per MEMORY.md
    -- Status flags
    status          TEXT    NOT NULL DEFAULT 'active', -- 'active'|'retired'|'dead'|'pending'
    is_approved     INTEGER NOT NULL DEFAULT 0,     -- 0|1 boolean
    approved_by     TEXT,                           -- discord_id of approving staff
    approved_at     TEXT,
    retirement_eligible_at TEXT,                    -- ISO-8601, set when xp_total >= xp_cap
    -- Ingrained Discipline Flaw
    has_ingrained_flaw  INTEGER NOT NULL DEFAULT 0,
    ingrained_xp_used   INTEGER NOT NULL DEFAULT 0, -- max 15
    -- IC profile
    profile_image_url   TEXT,
    profile_blurb       TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_characters_discord ON characters(discord_id);
CREATE INDEX IF NOT EXISTS idx_characters_status  ON characters(status);

-- ── Play periods (the XP windows) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS play_periods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT    NOT NULL,               -- e.g. "Night 42 — Dusk to Midnight"
    period_type     TEXT    NOT NULL DEFAULT 'night', -- 'night'|'downtime'|'timeskip'
    phase           TEXT    NOT NULL DEFAULT 'full', -- 'full'|'dusk'|'midnight'
    opens_at        TEXT    NOT NULL,               -- ISO-8601 datetime
    closes_at       TEXT    NOT NULL,               -- ISO-8601 datetime
    is_active       INTEGER NOT NULL DEFAULT 0,     -- only one active at a time
    created_by      TEXT    NOT NULL,               -- staff discord_id
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── XP earn criteria (editable by staff) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS criteria (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    label               TEXT    NOT NULL,
    description         TEXT    NOT NULL DEFAULT '',
    xp_value            INTEGER NOT NULL,
    category            TEXT    NOT NULL DEFAULT 'player', -- 'base'|'player'|'staff'|'helper'
    requires_rp_links   INTEGER NOT NULL DEFAULT 1,        -- 0|1
    requires_text_note  INTEGER NOT NULL DEFAULT 0,        -- 0|1
    active              INTEGER NOT NULL DEFAULT 1,        -- 0|1
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Seed default criteria (NYbN ruleset)
INSERT OR IGNORE INTO criteria (id, label, xp_value, category, requires_rp_links, requires_text_note, active, sort_order) VALUES
    (1, 'Posting',          3, 'base',   1, 0, 1, 1),
    (2, 'Monstrous Action', 1, 'player', 1, 0, 1, 2),
    (3, 'Altruistic Action',1, 'player', 1, 0, 1, 3),
    (4, 'Combat',           1, 'player', 1, 0, 1, 4),
    (5, 'Event',            1, 'player', 1, 0, 1, 5),
    (6, 'Writing Prompt',   1, 'player', 1, 0, 1, 6),
    (7, 'Sabbat Character', 1, 'player', 1, 0, 0, 7),
    (8, 'Staff Activity',   1, 'staff',  0, 0, 1, 8),
    (9, 'Helper Activity',  1, 'helper', 0, 1, 1, 9);

-- ── XP claims (earn submissions) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS xp_claims (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id            INTEGER NOT NULL REFERENCES characters(id),
    play_period_id          INTEGER NOT NULL REFERENCES play_periods(id),
    -- Criteria snapshot (JSON: [{criteria_id, label, xp_value_at_submission}])
    claimed_criteria        TEXT    NOT NULL DEFAULT '[]',
    rp_links                TEXT    NOT NULL DEFAULT '[]', -- JSON list of Discord URLs
    -- Staff/helper path
    path                    TEXT    NOT NULL DEFAULT 'none', -- 'none'|'staff'|'helper'
    helper_note             TEXT,
    staff_claim_conflict    INTEGER NOT NULL DEFAULT 0,     -- 0|1 flag
    -- XP total for this claim (snapshotted)
    xp_claimed              INTEGER NOT NULL DEFAULT 0,
    -- Review
    status                  TEXT    NOT NULL DEFAULT 'pending', -- 'pending'|'approved'|'rejected'
    reviewed_by             TEXT,                               -- staff discord_id
    reviewed_at             TEXT,
    rejection_reason        TEXT,
    submitted_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_xp_claims_character  ON xp_claims(character_id);
CREATE INDEX IF NOT EXISTS idx_xp_claims_period     ON xp_claims(play_period_id);
CREATE INDEX IF NOT EXISTS idx_xp_claims_status     ON xp_claims(status);

-- ── Spend requests (XP purchases) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spend_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id        INTEGER NOT NULL REFERENCES characters(id),
    -- Trait info
    category            TEXT    NOT NULL,   -- matches xp_costs.json key
    trait_name          TEXT    NOT NULL,   -- e.g. "Dominate"
    current_dots        INTEGER NOT NULL DEFAULT 0,
    new_dots            INTEGER NOT NULL,
    verified_cost       INTEGER NOT NULL,   -- XP cost calculated and locked at submission
    -- Special flags
    is_ingrained        INTEGER NOT NULL DEFAULT 0, -- uses Ingrained Discipline Flaw budget
    humanity_conditions TEXT,                       -- JSON checklist for Humanity purchases
    -- Review
    status              TEXT    NOT NULL DEFAULT 'pending', -- 'pending'|'approved'|'rejected'
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    rejection_reason    TEXT,
    note                TEXT,               -- player justification
    submitted_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_spend_requests_character ON spend_requests(character_id);
CREATE INDEX IF NOT EXISTS idx_spend_requests_status    ON spend_requests(status);

-- ── Ledger entries (append-only XP history) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS ledger_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id    INTEGER NOT NULL REFERENCES characters(id),
    entry_type      TEXT    NOT NULL,   -- 'earn'|'spend'|'adjustment'|'reversal'
    xp_delta        INTEGER NOT NULL,   -- positive = earn, negative = spend/deduct
    reference_id    INTEGER,            -- xp_claims.id or spend_requests.id
    reference_type  TEXT,               -- 'claim'|'spend'|'manual'
    note            TEXT,
    created_by      TEXT    NOT NULL,   -- staff discord_id or 'system'
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ledger_character ON ledger_entries(character_id);

-- ── Audit log (staff action trail) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id        TEXT    NOT NULL,   -- staff discord_id
    action          TEXT    NOT NULL,   -- e.g. 'approve_claim', 'reject_spend'
    target_type     TEXT    NOT NULL,   -- 'character'|'claim'|'spend'|'criteria'|'period'
    target_id       INTEGER,
    before_json     TEXT,               -- state before change
    after_json      TEXT,               -- state after change
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id);

-- ── Coteries ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coteries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    -- Domain values (V5 coterie mechanics)
    chasse          INTEGER NOT NULL DEFAULT 1,
    lien            INTEGER NOT NULL DEFAULT 0,
    portillon       INTEGER NOT NULL DEFAULT 0,
    -- Status
    status          TEXT    NOT NULL DEFAULT 'active', -- 'active'|'disbanded'
    discord_role_id TEXT,               -- optional Discord role ID
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Coterie memberships ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coterie_memberships (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id      INTEGER NOT NULL REFERENCES coteries(id),
    character_id    INTEGER NOT NULL REFERENCES characters(id),
    role            TEXT    NOT NULL DEFAULT 'member', -- 'leader'|'member'
    joined_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (coterie_id, character_id)
);

CREATE INDEX IF NOT EXISTS idx_coterie_memberships_coterie   ON coterie_memberships(coterie_id);
CREATE INDEX IF NOT EXISTS idx_coterie_memberships_character ON coterie_memberships(character_id);

-- ── Coterie merits (individual member merits) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS coterie_merits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id      INTEGER NOT NULL REFERENCES coteries(id),
    character_id    INTEGER NOT NULL REFERENCES characters(id),
    merit_name      TEXT    NOT NULL,
    dots            INTEGER NOT NULL DEFAULT 1,
    merit_type      TEXT    NOT NULL DEFAULT 'purchased', -- 'purchased'|'creation'|'donated'
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Coterie flaws ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coterie_flaws (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id      INTEGER NOT NULL REFERENCES coteries(id),
    flaw_name       TEXT    NOT NULL,
    dots            INTEGER NOT NULL DEFAULT 1,
    creation_grant  INTEGER NOT NULL DEFAULT 0, -- XP/dot grant from taking this flaw
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Coterie formation requests ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coterie_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_by    TEXT    NOT NULL,   -- discord_id of requesting player
    proposed_name   TEXT    NOT NULL,
    member_ids      TEXT    NOT NULL DEFAULT '[]', -- JSON list of character_ids
    note            TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending', -- 'pending'|'approved'|'rejected'
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    coterie_id      INTEGER REFERENCES coteries(id), -- set on approval
    submitted_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Coterie XP spends ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coterie_spends (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id      INTEGER NOT NULL REFERENCES coteries(id),
    trait_name      TEXT    NOT NULL,   -- e.g. "chasse", "lien", "portillon"
    current_dots    INTEGER NOT NULL,
    new_dots        INTEGER NOT NULL,
    total_cost      INTEGER NOT NULL,   -- total XP across all members
    per_member_cost INTEGER NOT NULL,
    contributions   TEXT    NOT NULL DEFAULT '{}', -- JSON {character_id: xp_contributed}
    status          TEXT    NOT NULL DEFAULT 'pending',
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    submitted_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Hunting sites ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hunting_sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    borough         TEXT    NOT NULL,   -- NYC borough / area
    description     TEXT    NOT NULL DEFAULT '',
    -- Predator type DCs (JSON: {"Alleycat": 2, "Bagger": 3, ...})
    predator_dcs    TEXT    NOT NULL DEFAULT '{}',
    resonance       TEXT,               -- dominant resonance type
    blood_quality   INTEGER NOT NULL DEFAULT 1,  -- 1–5
    -- Ownership
    coterie_id      INTEGER REFERENCES coteries(id),
    is_contested    INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Bot outbox (cross-process commands web → bot) ────────────────────────────
CREATE TABLE IF NOT EXISTS bot_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    command         TEXT    NOT NULL,   -- e.g. 'send_approval_dm', 'update_roster_post'
    payload         TEXT    NOT NULL DEFAULT '{}', -- JSON
    status          TEXT    NOT NULL DEFAULT 'pending', -- 'pending'|'processing'|'done'|'failed'
    attempts        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    processed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_bot_outbox_status ON bot_outbox(status, created_at);
