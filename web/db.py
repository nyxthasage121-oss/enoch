"""db.py — Database connection, migrations, and query helpers.

Uses libsql-experimental which mirrors the sqlite3 API for local files
and speaks HTTP to Turso for production.
Falls back to stdlib sqlite3 when libsql-experimental is not installed
(no pre-built wheels for every platform/Python version).
"""
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import libsql_experimental as libsql
except ModuleNotFoundError:
    import sqlite3 as libsql  # type: ignore[no-redef]  # same API for local SQLite

from .config import settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# Ingrained Discipline Flaw XP budget — matches packages/rules/xp_costs.json
_INGRAINED_XP_CAP = 15


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_factory(cursor, row):
    """Convert libsql tuple rows to plain dicts keyed by column name."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _j(value) -> str:
    """Serialize value to compact JSON string."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse(row: dict | None, *fields: str) -> dict | None:
    """In-place parse JSON string fields; return row unchanged if None."""
    if row is None:
        return None
    for f in fields:
        if isinstance(row.get(f), str):
            try:
                row[f] = json.loads(row[f])
            except (json.JSONDecodeError, TypeError):
                pass
    return row


# ── Connection ────────────────────────────────────────────────────────────────

def _connect() -> libsql.Connection:
    url   = settings.DATABASE_URL
    token = settings.TURSO_AUTH_TOKEN
    if url.startswith("libsql") or url.startswith("https://"):
        conn = libsql.connect(database=url, auth_token=token)
    else:
        conn = libsql.connect(database=url)
    conn.row_factory = _row_factory
    return conn


@contextmanager
def get_db():
    """Yield a libsql connection; commit on success, rollback on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ── Migrations ────────────────────────────────────────────────────────────────

def run_migrations() -> None:
    """Apply any pending numbered *.sql files from the migrations/ directory."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

    with get_db() as conn:
        applied = {
            row["filename"]
            for row in conn.execute("SELECT filename FROM _migrations").fetchall()
        }

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        log.info("Applying migration: %s", path.name)
        sql = path.read_text(encoding="utf-8")
        # Strip line comments before splitting on ";" so semicolons inside
        # comments (e.g. "-- note; more note") don't create phantom statements.
        import re as _re
        sql_clean = _re.sub(r"--[^\n]*", "", sql)
        statements = [s.strip() for s in sql_clean.split(";") if s.strip()]
        with get_db() as conn:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (path.name,))
        log.info("Applied: %s", path.name)


# ── Player Profiles ───────────────────────────────────────────────────────────

def get_player(conn, discord_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM player_profiles WHERE discord_id=?", (discord_id,)
    ).fetchone()


# ── Staff role + permission matrix ───────────────────────────────────────────

# Canonical role list — order matters for the admin dropdown.
STAFF_ROLES = ("lead_st", "co_st", "reviewer", "helper")

# Permission matrix. Roles are checked against permission keys. Anything
# not listed defaults to denied. Keep keys short + verb-noun-y so route
# wiring reads naturally (require_permission("manage_settings"), etc.).
STAFF_PERMISSIONS: dict[str, set[str]] = {
    "lead_st": {
        "approve_claim", "approve_spend", "approve_character", "reject_character",
        "edit_character", "delete_character", "adjust_xp",
        "manage_period", "manage_coterie", "manage_criteria", "manage_site",
        "manage_map", "manage_settings", "manage_roles",
    },
    "co_st": {
        "approve_claim", "approve_spend", "approve_character", "reject_character",
        "edit_character", "delete_character", "adjust_xp",
        "manage_period", "manage_coterie", "manage_criteria", "manage_site",
        "manage_map",
        # No manage_settings or manage_roles
    },
    "reviewer": {
        "approve_claim", "approve_spend",
    },
    "helper": set(),  # Read-only access on the dashboard
}


def staff_role_has_permission(role: str | None, permission: str) -> bool:
    """True when the given role grants the given permission. Unknown
    roles + missing roles always return False."""
    if not role:
        return False
    return permission in STAFF_PERMISSIONS.get(role, set())


def get_staff_role(conn, discord_id: str) -> str | None:
    row = conn.execute(
        "SELECT staff_role FROM player_profiles WHERE discord_id=?",
        (discord_id,),
    ).fetchone()
    return row["staff_role"] if row else None


def set_settings_admin(conn, discord_id: str, enabled: bool, actor_id: str) -> dict | None:
    """Grant or revoke the settings-admin flag (migration 024). Audited.
    Returns the updated player_profiles row or None if no such player."""
    before = get_player(conn, discord_id)
    if before is None:
        raise ValueError(f"Player {discord_id} not found")
    conn.execute(
        "UPDATE player_profiles SET settings_admin=?, updated_at=? WHERE discord_id=?",
        (1 if enabled else 0, _now(), discord_id),
    )
    write_audit(conn, actor_id, "set_settings_admin", "player_profile", None,
                before={"settings_admin": bool(before.get("settings_admin"))},
                after={"settings_admin": bool(enabled), "discord_id": discord_id})
    return get_player(conn, discord_id)


def set_staff_role(conn, discord_id: str, role: str | None, actor_id: str) -> dict | None:
    """Assign or clear a staff role. Pass role=None to revoke. Writes
    an audit log entry so it's always traceable who changed who."""
    if role is not None and role not in STAFF_ROLES:
        raise ValueError(f"Unknown staff role: {role!r}")
    before = get_player(conn, discord_id)
    if before is None:
        raise ValueError(f"Player {discord_id} not found")
    conn.execute(
        "UPDATE player_profiles SET staff_role=?, updated_at=? WHERE discord_id=?",
        (role, _now(), discord_id),
    )
    write_audit(conn, actor_id, "set_staff_role", "player_profile", None,
                before={"staff_role": before.get("staff_role")},
                after={"staff_role": role, "discord_id": discord_id})
    return get_player(conn, discord_id)


def upsert_player(conn, discord_id: str, username: str, cubby_channel: str | None = None) -> dict:
    now = _now()
    conn.execute("""
        INSERT INTO player_profiles (discord_id, username, cubby_channel, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET
            username      = excluded.username,
            cubby_channel = COALESCE(excluded.cubby_channel, player_profiles.cubby_channel),
            updated_at    = excluded.updated_at
    """, (discord_id, username, cubby_channel, now, now))
    return get_player(conn, discord_id)


def list_all_players(conn) -> list[dict]:
    """Staff: all player profiles with character stats."""
    return conn.execute("""
        SELECT
            pp.*,
            COUNT(c.id)                                       AS character_count,
            SUM(CASE WHEN c.status='active' THEN 1 ELSE 0 END) AS active_count
        FROM player_profiles pp
        LEFT JOIN characters c ON c.discord_id = pp.discord_id
        GROUP BY pp.discord_id
        ORDER BY pp.username COLLATE NOCASE
    """).fetchall()


def sweep_retirements(conn) -> list[dict]:
    """Auto-retire characters whose 6-month retirement window has elapsed.

    Finds active characters with `retirement_eligible_at` older than
    settings.RETIREMENT_WINDOW_DAYS, flips status to 'retired', writes a
    ledger entry + audit row, and enqueues a 'character_retired' DM.
    Idempotent — safe to call frequently. Returns newly-retired character rows.
    """
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=settings.RETIREMENT_WINDOW_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute("""
        SELECT * FROM characters
        WHERE status='active'
          AND retirement_eligible_at IS NOT NULL
          AND retirement_eligible_at <= ?
    """, (cutoff,)).fetchall()

    retired: list[dict] = []
    now = _now()
    for char in rows:
        conn.execute(
            "UPDATE characters SET status='retired', updated_at=? WHERE id=?",
            (now, char["id"]),
        )
        conn.execute("""
            INSERT INTO ledger_entries
                (character_id, entry_type, xp_delta, reference_type, note, created_by, created_at)
            VALUES (?, 'adjustment', 0, 'retirement', ?, 'system', ?)
        """, (char["id"], "Auto-retired after 6-month retirement window.", now))
        write_audit(conn, "system", "auto_retire", "character", char["id"],
                    before={"status": "active"},
                    after={"status": "retired",
                           "retirement_eligible_at": char["retirement_eligible_at"]})
        # Coterie hook — auto-retirement also suspends contributions so
        # the inactive member's dots stop counting toward effective ratings.
        # Forward-declared via lazy resolution since suspend_member_contributions
        # is defined later in this module.
        try:
            suspend_member_contributions(conn, char["id"], actor_id="system")
        except NameError:
            # Defensive: if the function isn't loaded yet (shouldn't happen at
            # runtime), don't break retirement sweep.
            pass
        enqueue_bot(conn, "character_retired", {
            "character_id": char["id"],
            "discord_id":   char["discord_id"],
            "name":         char["name"],
        })
        retired.append(char)
    return retired


def adjust_xp_manual(
    conn,
    character_id: int,
    delta: int,
    note: str,
    staff_id: str,
) -> dict:
    """Manual XP grant or deduction by staff.  delta > 0 = grant, < 0 = deduct."""
    char = get_character(conn, character_id)
    if char is None:
        raise ValueError(f"Character {character_id} not found")
    if not note.strip():
        raise ValueError("A note is required for manual adjustments.")
    now       = _now()
    new_total = max(0, char["xp_total"] + delta)
    conn.execute(
        "UPDATE characters SET xp_total=?, updated_at=? WHERE id=?",
        (new_total, now, character_id),
    )
    conn.execute("""
        INSERT INTO ledger_entries
            (character_id, entry_type, xp_delta, reference_type, note, created_by, created_at)
        VALUES (?, 'adjustment', ?, 'manual', ?, ?, ?)
    """, (character_id, delta, note, staff_id, now))
    write_audit(conn, staff_id, "adjust_xp", "character", character_id,
                before={"xp_total": char["xp_total"]},
                after={"xp_total": new_total, "delta": delta, "note": note})
    return get_character(conn, character_id)


# ── Characters ────────────────────────────────────────────────────────────────

def _enrich_char(row: dict | None) -> dict | None:
    row = _parse(row, "sheet_json", "in_memoriam")
    if row:
        # Lazy schema migration — bumps legacy sheets up to the current
        # version on every read. Lives in sheet_migrations.py to keep
        # the chain isolated and individually testable.
        from .sheet_migrations import migrate_sheet
        row["sheet_json"] = migrate_sheet(row.get("sheet_json") or {})
        row["xp_available"] = row["xp_total"] - row["xp_spent"]
    return row


def get_character(conn, character_id: int) -> dict | None:
    return _enrich_char(
        conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    )


def get_character_for_player(conn, character_id: int, discord_id: str) -> dict | None:
    """Ownership-gated fetch — returns None if character belongs to someone else."""
    return _enrich_char(
        conn.execute(
            "SELECT * FROM characters WHERE id=? AND discord_id=?",
            (character_id, discord_id)
        ).fetchone()
    )


def list_player_characters(conn, discord_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM characters WHERE discord_id=? ORDER BY name", (discord_id,)
    ).fetchall()
    return [_enrich_char(r) for r in rows]


def list_characters_near_cap(conn, threshold_xp: int = 30) -> list[dict]:
    """Approved + active characters within `threshold_xp` of their cap.
    Sorted closest-first so the dashboard tile + roster filter both
    surface the same urgent rows. Excludes already-retired characters
    so they don't trip the warning."""
    return conn.execute(
        """
        SELECT c.*, pp.username AS player_username,
               (c.xp_cap - c.xp_total) AS xp_to_cap
        FROM characters c
        LEFT JOIN player_profiles pp ON pp.discord_id = c.discord_id
        WHERE c.is_approved = 1
          AND c.status = 'active'
          AND (c.xp_cap - c.xp_total) BETWEEN 0 AND ?
        ORDER BY xp_to_cap ASC, c.name
        """,
        (threshold_xp,),
    ).fetchall()


def list_characters(conn, status: str | None = None, clan: str | None = None) -> list[dict]:
    """Staff: all characters with player username + a `last_activity_at`
    timestamp (max of last claim, last spend, last ledger entry).
    NULL means the character has never had recorded XP activity."""
    clauses, params = [], []
    if status:
        clauses.append("c.status=?")
        params.append(status)
    if clan:
        clauses.append("c.clan=?")
        params.append(clan)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"""
        SELECT
          c.*,
          pp.username AS player_username,
          (
            SELECT MAX(t.ts) FROM (
              SELECT MAX(submitted_at) AS ts FROM xp_claims      WHERE character_id = c.id
              UNION ALL
              SELECT MAX(submitted_at) AS ts FROM spend_requests WHERE character_id = c.id
              UNION ALL
              SELECT MAX(created_at)   AS ts FROM ledger_entries WHERE character_id = c.id
            ) t
          ) AS last_activity_at
        FROM characters c
        LEFT JOIN player_profiles pp ON pp.discord_id = c.discord_id
        {where}
        ORDER BY c.name
    """, params).fetchall()
    return [_enrich_char(r) for r in rows]


def create_character(
    conn,
    discord_id: str,
    name: str,
    clan: str,
    predator_type: str | None = None,
    concept: str | None = None,
    sire: str | None = None,
    covenant: str | None = None,
    sheet_json: dict | None = None,
    has_ingrained_flaw: bool = False,
    character_type: str = "kindred",
    revenant_family: str | None = None,
    ghoul_regnant: str | None = None,
) -> dict:
    now = _now()
    if character_type not in ("kindred", "mortal", "ghoul", "revenant"):
        character_type = "kindred"
    cur = conn.execute("""
        INSERT INTO characters
            (discord_id, name, clan, predator_type, concept, sire, covenant,
             sheet_json, has_ingrained_flaw,
             character_type, revenant_family, ghoul_regnant,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        discord_id, name, clan, predator_type, concept, sire, covenant,
        _j(sheet_json or {}), int(has_ingrained_flaw),
        character_type, revenant_family, ghoul_regnant,
        now, now,
    ))
    return get_character(conn, cur.lastrowid)


def update_character(conn, character_id: int, **fields) -> dict:
    """Update whitelisted character fields."""
    ALLOWED = {
        "name", "clan", "predator_type", "concept", "sire", "covenant",
        "sheet_json", "status", "is_approved", "approved_by", "approved_at",
        "retirement_eligible_at", "has_ingrained_flaw", "ingrained_xp_used",
        "profile_image_url", "profile_blurb", "rejection_reason", "rejected_at",
        "character_type", "revenant_family", "ghoul_regnant",
        "is_draft", "submission_notes",
        "character_tier", "ancilla_mode",
        "im_generation", "im_discipline_spread", "in_memoriam",
        "ambition", "desire", "profession",
        "true_age", "apparent_age", "pronouns", "backstory",
        "st_notes",
        # Migration 018 — profile lock + ingrained discipline tracking
        "profile_locked", "ingrained_discipline",
        # Migration 026 — post-wizard sheet-completion flag (replaces the
        # old `_post_wizard` sentinel that lived inside sheet_json)
        "post_wizard",
    }
    # JSON-serialize the in-memoriam blob before persistence
    if "in_memoriam" in fields and isinstance(fields["in_memoriam"], (dict, list)):
        fields = dict(fields)
        fields["in_memoriam"] = _j(fields["in_memoriam"])
    safe = {k: v for k, v in fields.items() if k in ALLOWED}
    if not safe:
        return get_character(conn, character_id)
    if "sheet_json" in safe and isinstance(safe["sheet_json"], dict):
        safe["sheet_json"] = _j(safe["sheet_json"])
    safe["updated_at"] = _now()
    sets   = ", ".join(f"{k}=?" for k in safe)
    params = list(safe.values()) + [character_id]
    conn.execute(f"UPDATE characters SET {sets} WHERE id=?", params)
    return get_character(conn, character_id)


def approve_character(conn, character_id: int, reviewer_id: str) -> dict:
    now = _now()
    conn.execute("""
        UPDATE characters
        SET is_approved=1, status='active', approved_by=?, approved_at=?, updated_at=?,
            review_started_at=NULL, review_started_by=NULL
        WHERE id=?
    """, (reviewer_id, now, now, character_id))
    char = get_character(conn, character_id)
    write_audit(conn, reviewer_id, "approve_character", "character", character_id,
                after={"is_approved": 1, "status": "active"})
    enqueue_bot(conn, "character_approved", {
        "character_id": character_id,
        "discord_id": char["discord_id"],
    })
    return char


def reject_character(conn, character_id: int, reviewer_id: str, reason: str) -> dict:
    now = _now()
    # Reset to pending so player can resubmit; store reason for player to see.
    # Also clear is_approved + review_started_at so the row can flow through
    # the queue again (otherwise a previously-approved character can land in
    # is_approved=1 + status='pending' which masks the Returned chip and
    # leaves the player unable to resubmit).
    conn.execute("""
        UPDATE characters
        SET status='pending',
            is_approved=0,
            approved_by=NULL,
            approved_at=NULL,
            review_started_at=NULL,
            review_started_by=NULL,
            rejection_reason=?, rejected_at=?, updated_at=?
        WHERE id=?
    """, (reason, now, now, character_id))
    char = get_character(conn, character_id)
    write_audit(conn, reviewer_id, "reject_character", "character", character_id,
                after={"status": "pending", "reason": reason})
    enqueue_bot(conn, "character_rejected", {
        "character_id": character_id,
        "discord_id":   char["discord_id"],
        "reason":       reason,
    })
    return char


def delete_character(conn, character_id: int) -> None:
    """Hard-delete a character and all related records.

    Deletes in FK-safe order since SQLite foreign key enforcement may be off.
    Only call after verifying no other system has a critical dependency.
    """
    conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (character_id,))
    conn.execute("DELETE FROM spend_requests WHERE character_id=?", (character_id,))
    conn.execute("DELETE FROM xp_claims WHERE character_id=?", (character_id,))
    conn.execute("DELETE FROM coterie_memberships WHERE character_id=?", (character_id,))
    conn.execute("DELETE FROM characters WHERE id=?", (character_id,))


# ── Chronicle Settings ────────────────────────────────────────────────────────

def get_settings(conn) -> dict | None:
    row = conn.execute("SELECT * FROM chronicle_settings WHERE id=1").fetchone()
    return _parse(row, "revenant_families", "homebrew_tier_budgets",
                  "unlocked_predator_types")


# Chronicle ruleset constants — gives the rest of the app a single place
# to look up valid values + the V5 RAW defaults.
RULESETS = ("standard", "homebrew", "in_memoriam")

# Per-tier budget defaults used when the chronicle is on the standard
# ruleset or hasn't customized that tier yet. Values reflect V5 RAW
# starting allotments for each character archetype.
#
# Kindred tiers map to V5's Sea of Time (Corebook p.130):
#   fledgling — Childer, embraced last 15 yr. Baseline allocation,
#               no XP bonus. Blood Potency 1 (or 0 if thin-blood).
#   thinblood — variant of Fledgling. 14th-16th gen, BP 0, no clan
#               disciplines (uses Alchemy instead). Plus 1-3 Thin-Blood
#               Merits + matching Thin-Blood Flaws on top of the standard
#               7 advantage / 2 flaw allocation — Steward verifies at
#               approval since those slots are special.
#   neonate   — embraced 1940 to a decade ago. BP 1. +15 XP over baseline.
#   ancilla   — embraced 1780-1940. BP 2. +35 XP, +2 advantages,
#               +2 flaws, -1 Humanity over baseline.
#
# The "merits + advantages + backgrounds" total is the V5 RAW pool of
# 7 advantage points; the split into three buckets is for the wizard
# sidebar (combined-pool admin override since Steward UX revision).
_TIER_DEFAULTS = {
    "mortal":    {"xp": 50,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "ghoul":     {"xp": 60,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "revenant":  {"xp": 75,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    # Childer / Fledgling — baseline Kindred, no Sea of Time XP bonus.
    "fledgling": {"xp": 60,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    # Thin-Blood — same XP as Fledgling but uses Alchemy instead of
    # in-clan Disciplines. Steward verifies the 1-3 Thin-Blood
    # Merits/Flaws at approval — they don't eat the standard pool.
    "thinblood": {"xp": 60,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "neonate":   {"xp": 75,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "ancilla":   {"xp": 110, "merits": 3, "advantages": 3, "backgrounds": 3, "flaw_cap": 4},
}


def tier_budget(settings: dict | None, tier: str) -> dict:
    """Return the active budget for a tier, honoring the chronicle's
    homebrew_tier_budgets overrides whenever they're present, regardless
    of which ruleset is active. The IM ruleset is purely about Ancilla
    flow — chronicles can still tune budgets for other tiers under it.
    Falls back to V5 RAW defaults when no overrides are saved."""
    tier_key = (tier or "neonate").lower()
    defaults = dict(_TIER_DEFAULTS.get(tier_key, _TIER_DEFAULTS["neonate"]))
    if not settings:
        return defaults
    # Standard ruleset = pure V5 RAW — ignore any stored overrides
    # entirely so chronicles can "reset" by flipping to standard.
    ruleset = settings.get("active_ruleset") or "standard"
    if ruleset == "standard":
        return defaults
    overrides = settings.get("homebrew_tier_budgets") or {}
    tier_overrides = overrides.get(tier_key) if isinstance(overrides, dict) else None
    if not isinstance(tier_overrides, dict):
        return defaults
    # Merge each field separately so a partial override (only xp set,
    # everything else from defaults) works cleanly.
    out = dict(defaults)
    for key in ("xp", "merits", "advantages", "backgrounds", "flaw_cap"):
        if key in tier_overrides:
            try:
                out[key] = int(tier_overrides[key])
            except (TypeError, ValueError):
                pass
    return out


def upsert_settings(conn, actor_id: str | None = None, **kwargs) -> dict:
    ALLOWED = {
        "server_start_date", "xp_frequency", "night_start_hour",
        "timeskip_interval", "midnight_split",
        "require_sheet_on_create",
        "use_homebrew_rules", "homebrew_starting_xp",
        "homebrew_merit_budget", "homebrew_advantage_budget",
        "homebrew_background_budget", "homebrew_flaw_cap",
        "revenants_enabled", "revenant_families",
        # Ruleset selector + per-tier budgets (migration 016)
        "active_ruleset", "homebrew_tier_budgets",
        # Predator-type opt-in list (migration 021)
        "unlocked_predator_types",
        # Auto period-generation toggle (migration 025)
        "auto_create_periods_enabled",
    }
    # Validate ruleset before persisting — guard against typo'd POSTs.
    if "active_ruleset" in kwargs and kwargs["active_ruleset"] not in RULESETS:
        raise ValueError(f"Unknown ruleset: {kwargs['active_ruleset']!r}")
    # Serialize lists/dicts before insert (revenant_families is a JSON column)
    safe_raw = {k: v for k, v in kwargs.items() if k in ALLOWED}
    if "revenant_families" in safe_raw and isinstance(safe_raw["revenant_families"], (list, dict)):
        safe_raw["revenant_families"] = _j(safe_raw["revenant_families"])
    if "homebrew_tier_budgets" in safe_raw and isinstance(safe_raw["homebrew_tier_budgets"], (list, dict)):
        safe_raw["homebrew_tier_budgets"] = _j(safe_raw["homebrew_tier_budgets"])
    if "unlocked_predator_types" in safe_raw and isinstance(safe_raw["unlocked_predator_types"], (list, dict)):
        safe_raw["unlocked_predator_types"] = _j(safe_raw["unlocked_predator_types"])
    kwargs = safe_raw
    safe = {k: v for k, v in kwargs.items() if k in ALLOWED}
    if not safe:
        return get_settings(conn)
    safe["updated_at"] = _now()
    before = get_settings(conn) or {}
    if not before:
        safe.setdefault("server_start_date", _now()[:10])
        cols         = ", ".join(["id"] + list(safe.keys()))
        placeholders = "1, " + ", ".join("?" * len(safe))
        conn.execute(
            f"INSERT INTO chronicle_settings ({cols}) VALUES ({placeholders})",
            list(safe.values())
        )
    else:
        sets = ", ".join(f"{k}=?" for k in safe)
        conn.execute(
            f"UPDATE chronicle_settings SET {sets} WHERE id=1", list(safe.values())
        )
    after = get_settings(conn)
    # Audit chronicle setting changes — only log fields that actually
    # changed so the audit row stays compact + searchable.
    if actor_id and after is not None:
        changed = {k: after.get(k) for k in safe.keys()
                   if k != "updated_at" and before.get(k) != after.get(k)}
        if changed:
            write_audit(conn, actor_id, "upsert_settings", "chronicle_settings", 1,
                        after=changed)
    return after


# ── Chronicle Restrictions (component bans / unlocks) ─────────────────────────
#
# Generic component-restriction table (migration 022). Two modes:
#   'banned'   — normally-allowed component is forbidden this chronicle.
#   'unlocked' — default-restricted component is allowed this chronicle.
#
# The "default-restricted" set lives in Python (e.g. v5_traits.py's
# V5_RESTRICTED_PREDATOR_TYPES) so this table can stay shape-agnostic.

def list_restrictions(conn, component_type: str | None = None) -> list[dict]:
    """Return restriction rows, optionally filtered by component_type."""
    if component_type:
        return conn.execute(
            "SELECT * FROM chronicle_restrictions WHERE component_type=? "
            "ORDER BY component_id",
            (component_type,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM chronicle_restrictions "
        "ORDER BY component_type, component_id"
    ).fetchall()


def get_restriction(conn, component_type: str, component_id: str,
                    mode: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM chronicle_restrictions "
        "WHERE component_type=? AND component_id=? AND mode=?",
        (component_type, component_id, mode),
    ).fetchone()


def is_component_allowed(conn, component_type: str, component_id: str,
                         default_restricted: set[str] | None = None) -> bool:
    """Decide whether a component is allowed in this chronicle.

    For components in `default_restricted` (per-type Python constants):
        allowed iff there's an 'unlocked' row.
    Otherwise:
        allowed iff there's NO 'banned' row.

    This dual model lets the same table express both "ban this normally-
    allowed thing" and "unlock this normally-banned thing"."""
    default_restricted = default_restricted or set()
    if component_id in default_restricted:
        return get_restriction(conn, component_type, component_id, "unlocked") is not None
    return get_restriction(conn, component_type, component_id, "banned") is None


def set_restriction(conn, component_type: str, component_id: str,
                    mode: str, reason: str | None = None,
                    updated_by: str | None = None) -> dict:
    """Upsert a restriction row. mode is 'banned' or 'unlocked'."""
    if mode not in ("banned", "unlocked"):
        raise ValueError(f"mode must be 'banned' or 'unlocked', got {mode!r}")
    now = _now()
    existing = get_restriction(conn, component_type, component_id, mode)
    if existing:
        conn.execute(
            "UPDATE chronicle_restrictions "
            "SET reason=?, updated_by=?, updated_at=? "
            "WHERE component_type=? AND component_id=? AND mode=?",
            (reason, updated_by, now, component_type, component_id, mode),
        )
    else:
        conn.execute(
            "INSERT INTO chronicle_restrictions "
            "(component_type, component_id, mode, reason, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (component_type, component_id, mode, reason, updated_by, now),
        )
    write_audit(conn, updated_by, "set_restriction",
                "chronicle_restriction", 0,
                after={"component_type": component_type,
                       "component_id": component_id, "mode": mode,
                       "reason": reason})
    return get_restriction(conn, component_type, component_id, mode)


def clear_restriction(conn, component_type: str, component_id: str,
                      mode: str, actor_id: str | None = None) -> None:
    """Remove a restriction row. Idempotent — no-op if it doesn't exist."""
    before = get_restriction(conn, component_type, component_id, mode)
    if not before:
        return
    conn.execute(
        "DELETE FROM chronicle_restrictions "
        "WHERE component_type=? AND component_id=? AND mode=?",
        (component_type, component_id, mode),
    )
    write_audit(conn, actor_id, "clear_restriction",
                "chronicle_restriction", before["id"],
                before={"component_type": component_type,
                        "component_id": component_id, "mode": mode})


# ── Play Periods ──────────────────────────────────────────────────────────────

def get_period(conn, period_id: int) -> dict | None:
    return conn.execute("SELECT * FROM play_periods WHERE id=?", (period_id,)).fetchone()


def get_active_period(conn) -> dict | None:
    return conn.execute(
        "SELECT * FROM play_periods WHERE is_active=1 LIMIT 1"
    ).fetchone()


def list_periods(conn, limit: int = 20) -> list[dict]:
    return conn.execute(
        "SELECT * FROM play_periods ORDER BY opens_at DESC LIMIT ?", (limit,)
    ).fetchall()


def list_upcoming_periods(conn, limit: int = 3) -> list[dict]:
    """Periods scheduled in the future (opens_at > now), excluding the active one.
    Ordered earliest-first so the next-on-deck is at index 0.
    """
    return conn.execute(
        """
        SELECT * FROM play_periods
        WHERE opens_at > ? AND is_active = 0
        ORDER BY opens_at ASC
        LIMIT ?
        """,
        (_now(), limit),
    ).fetchall()


def list_recent_closed_periods(conn, limit: int = 2) -> list[dict]:
    """Periods already closed (closes_at < now AND not active), most-recent first."""
    return conn.execute(
        """
        SELECT * FROM play_periods
        WHERE closes_at < ? AND is_active = 0
        ORDER BY closes_at DESC
        LIMIT ?
        """,
        (_now(), limit),
    ).fetchall()


# ── Period schedule templates ────────────────────────────────────────────────

def list_period_schedules(conn, active_only: bool = True) -> list[dict]:
    where = "WHERE active=1" if active_only else ""
    return conn.execute(
        f"SELECT * FROM period_schedules {where} ORDER BY created_at DESC"
    ).fetchall()


def get_period_schedule(conn, schedule_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM period_schedules WHERE id=?", (schedule_id,)
    ).fetchone()


def create_period_schedule(
    conn,
    *,
    name: str,
    anchor_at: str,
    period_type: str = "night",
    phase: str = "full",
    cadence_days: int = 14,
    duration_hours: int = 48,
    label_pattern: str = "Night {n}",
    created_by: str | None = None,
) -> dict:
    if cadence_days <= 0:
        raise ValueError("Cadence must be at least 1 day.")
    if duration_hours <= 0:
        raise ValueError("Duration must be at least 1 hour.")
    now = _now()
    cur = conn.execute("""
        INSERT INTO period_schedules
            (name, label_pattern, period_type, phase, cadence_days,
             anchor_at, duration_hours, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, label_pattern or "Night {n}", period_type, phase,
          cadence_days, anchor_at, duration_hours, created_by, now, now))
    row = get_period_schedule(conn, cur.lastrowid)
    if created_by:
        write_audit(conn, created_by, "create_period_schedule",
                    "period_schedule", row["id"],
                    after={"name": name, "cadence_days": cadence_days,
                           "anchor_at": anchor_at})
    return row


def delete_period_schedule(conn, schedule_id: int, actor_id: str | None = None) -> None:
    row = get_period_schedule(conn, schedule_id)
    conn.execute("DELETE FROM period_schedules WHERE id=?", (schedule_id,))
    if actor_id and row is not None:
        write_audit(conn, actor_id, "delete_period_schedule",
                    "period_schedule", schedule_id,
                    before={"name": row.get("name")})


def stamp_periods_from_schedule(
    conn,
    schedule_id: int,
    count: int,
    created_by: str,
) -> dict:
    """Generate `count` consecutive periods from this schedule. Picks up
    where the schedule left off — the first new period's opens_at is
    either the schedule's anchor (if nothing's been stamped yet) or one
    cadence after the last stamped period.

    Returns {created: int, periods: [dict, ...], skipped: int}. Periods
    that would collide with an existing window are skipped, not failed."""
    from datetime import datetime, timedelta

    if count < 1:
        raise ValueError("Must stamp at least 1 period.")
    if count > 52:
        raise ValueError("Refusing to stamp more than 52 periods in one shot.")

    schedule = get_period_schedule(conn, schedule_id)
    if schedule is None:
        raise ValueError(f"Schedule {schedule_id} not found")

    cadence = timedelta(days=int(schedule["cadence_days"]))
    duration = timedelta(hours=int(schedule["duration_hours"]))

    # Find the latest period created from this schedule's anchor or after.
    # We use opens_at lookups to avoid hard-linking periods → schedule rows.
    anchor_dt = datetime.fromisoformat(schedule["anchor_at"].replace("Z", "+00:00"))
    latest_row = conn.execute(
        "SELECT MAX(opens_at) AS latest FROM play_periods WHERE opens_at >= ?",
        (schedule["anchor_at"],),
    ).fetchone()
    latest = latest_row["latest"] if latest_row else None

    if latest:
        cursor = datetime.fromisoformat(latest.replace("Z", "+00:00")) + cadence
    else:
        cursor = anchor_dt

    created_rows: list[dict] = []
    skipped = 0
    n = int(schedule["next_n"])
    for _i in range(count):
        opens_at_iso = cursor.strftime("%Y-%m-%dT%H:%M:%SZ")
        closes_at_iso = (cursor + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Skip if a period already opens at this exact time (idempotent retry)
        clash = conn.execute(
            "SELECT 1 FROM play_periods WHERE opens_at=? LIMIT 1",
            (opens_at_iso,),
        ).fetchone()
        if clash:
            skipped += 1
            cursor += cadence
            continue
        label = (schedule["label_pattern"] or "Night {n}")\
            .replace("{n}", str(n))\
            .replace("{date}", cursor.strftime("%Y-%m-%d"))
        row = create_period(
            conn,
            label=label,
            period_type=schedule["period_type"],
            phase=schedule["phase"],
            opens_at=opens_at_iso,
            closes_at=closes_at_iso,
            created_by=created_by,
        )
        created_rows.append(row)
        n += 1
        cursor += cadence

    # Persist the next-counter so subsequent stamp calls keep numbering.
    conn.execute(
        "UPDATE period_schedules SET next_n=?, updated_at=? WHERE id=?",
        (n, _now(), schedule_id),
    )
    return {"created": len(created_rows), "periods": created_rows, "skipped": skipped}


def create_period(
    conn,
    label: str,
    period_type: str,
    phase: str,
    opens_at: str,
    closes_at: str,
    created_by: str,
) -> dict:
    cur = conn.execute("""
        INSERT INTO play_periods
            (label, period_type, phase, opens_at, closes_at, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (label, period_type, phase, opens_at, closes_at, created_by, _now()))
    return get_period(conn, cur.lastrowid)


def set_period_active(conn, period_id: int) -> dict:
    """Deactivate all periods, then activate the given one."""
    conn.execute("UPDATE play_periods SET is_active=0")
    conn.execute("UPDATE play_periods SET is_active=1 WHERE id=?", (period_id,))
    return get_period(conn, period_id)


def close_period(conn, period_id: int) -> None:
    conn.execute("UPDATE play_periods SET is_active=0 WHERE id=?", (period_id,))


def _next_period_label(prev_label: str) -> str:
    """Derive the next period's label by bumping the first integer in the
    previous one: 'Night 42 — Dusk to Midnight' -> 'Night 43 — Dusk to
    Midnight'. If there's no number to increment, fall back to a suffix so
    we never silently reuse the exact same label."""
    import re
    m = re.search(r"\d+", prev_label or "")
    if not m:
        return f"{(prev_label or 'Night').strip()} (cont.)"
    return f"{prev_label[:m.start()]}{int(m.group()) + 1}{prev_label[m.end():]}"


def auto_create_next_period_if_due(
    conn,
    *,
    lead_days: int = 2,
    now: str | None = None,
    actor_id: str = "system",
) -> dict | None:
    """Stamp the next play period when one is due, inferring the schedule
    from recent history instead of a stored template (migration 025).

    Gated by chronicle_settings.auto_create_periods_enabled. No-op unless:
      * the toggle is on,
      * there are >= 2 prior periods to infer a rhythm from,
      * we're within `lead_days` of the next period's computed opening.

    Cadence  = gap between the two most recent periods' opens_at.
    Duration = the latest period's own window (closes_at - opens_at).
    The new period inherits period_type + phase from the latest one and is
    created inactive — staff still press Activate to actually open it.

    Idempotent by construction: the "latest" period is always the furthest
    in the future, so once the next one is stamped the following call sees
    it as the anchor and won't fire again until that one's successor is due.
    This also means it never collides with periods staff scheduled by hand.

    `now` (ISO-8601 string) is injectable for testing; defaults to the wall
    clock. Returns the created period row, or None when nothing was due.
    """
    from datetime import datetime, timezone, timedelta

    settings = get_settings(conn)
    if not settings or not settings.get("auto_create_periods_enabled"):
        return None

    recent = conn.execute(
        "SELECT * FROM play_periods ORDER BY opens_at DESC LIMIT 2"
    ).fetchall()
    if len(recent) < 2:
        return None  # not enough history to infer a cadence

    def _parse(s: str) -> datetime:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    latest, prev = recent[0], recent[1]
    try:
        opens0, opens1 = _parse(latest["opens_at"]), _parse(prev["opens_at"])
        closes0 = _parse(latest["closes_at"])
    except (ValueError, TypeError, AttributeError):
        return None

    cadence  = opens0 - opens1
    duration = closes0 - opens0
    if cadence <= timedelta(0) or duration <= timedelta(0):
        return None  # out-of-order or zero-length history — don't guess

    now_dt = _parse(now) if now else datetime.now(timezone.utc)

    # Next slot is one cadence past the latest opening. If the chronicle
    # went dormant (the slot is already fully in the past), roll forward by
    # whole cadences so we never stamp an already-closed period. The cap is
    # a runaway guard for absurd data, not an expected path.
    next_opens = opens0 + cadence
    guard = 0
    while next_opens + duration <= now_dt and guard < 520:
        next_opens += cadence
        guard += 1

    if now_dt < next_opens - timedelta(days=lead_days):
        return None  # not yet within the lead window

    def _iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    opens_iso, closes_iso = _iso(next_opens), _iso(next_opens + duration)

    # Belt-and-suspenders: never double-stamp the same opening.
    if conn.execute(
        "SELECT 1 FROM play_periods WHERE opens_at=? LIMIT 1", (opens_iso,)
    ).fetchone():
        return None

    label = _next_period_label(latest["label"])
    row = create_period(
        conn,
        label=label,
        period_type=latest["period_type"],
        phase=latest["phase"],
        opens_at=opens_iso,
        closes_at=closes_iso,
        created_by=actor_id,
    )
    write_audit(
        conn, actor_id, "auto_create_period", "play_period", row["id"],
        after={"label": label, "opens_at": opens_iso, "closes_at": closes_iso,
               "inferred_cadence_days": round(cadence.total_seconds() / 86400, 2)},
    )
    return row


def sweep_period_closing_soon(conn, hours_threshold: int = 24) -> list[dict]:
    """Find active periods that close within `hours_threshold` and
    haven't had a closing reminder sent. For each, enqueue a
    `period_closing_soon` event for the bot to announce, then mark
    the reminder as sent so we don't fire twice.

    Returns the list of periods we just notified about.
    """
    now = datetime.now(timezone.utc)
    cutoff_iso = (now + timedelta(hours=hours_threshold)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = conn.execute(
        """
        SELECT * FROM play_periods
        WHERE is_active = 1
          AND closes_at > ?
          AND closes_at <= ?
          AND closing_reminder_sent_at IS NULL
        """,
        (now_iso, cutoff_iso),
    ).fetchall()

    for p in rows:
        enqueue_bot(conn, "period_closing_soon", {
            "period_id":   p["id"],
            "label":       p["label"],
            "period_type": p["period_type"],
            "phase":       p["phase"],
            "closes_at":   p["closes_at"],
        })
        conn.execute(
            "UPDATE play_periods SET closing_reminder_sent_at=? WHERE id=?",
            (now_iso, p["id"]),
        )
    return rows


# ── Criteria ──────────────────────────────────────────────────────────────────

def get_criterion(conn, criteria_id: int) -> dict | None:
    return conn.execute("SELECT * FROM criteria WHERE id=?", (criteria_id,)).fetchone()


def list_criteria(conn, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM criteria"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY sort_order, id"
    return conn.execute(sql).fetchall()


def create_criterion(
    conn,
    label: str,
    xp_value: int,
    category: str = "player",
    description: str = "",
    requires_rp_links: bool = True,
    requires_text_note: bool = False,
    active: bool = True,
    sort_order: int = 0,
) -> dict:
    now = _now()
    cur = conn.execute("""
        INSERT INTO criteria
            (label, xp_value, category, description,
             requires_rp_links, requires_text_note, active, sort_order,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        label, xp_value, category, description,
        int(requires_rp_links), int(requires_text_note),
        int(active), sort_order, now, now,
    ))
    return get_criterion(conn, cur.lastrowid)


def update_criterion(conn, criteria_id: int, **fields) -> dict:
    ALLOWED = {
        "label", "xp_value", "category", "description",
        "requires_rp_links", "requires_text_note", "active", "sort_order",
    }
    safe = {k: v for k, v in fields.items() if k in ALLOWED}
    if not safe:
        return get_criterion(conn, criteria_id)
    safe["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in safe)
    conn.execute(f"UPDATE criteria SET {sets} WHERE id=?", list(safe.values()) + [criteria_id])
    return get_criterion(conn, criteria_id)


# ── XP Claims ─────────────────────────────────────────────────────────────────

def _enrich_claim(row: dict | None) -> dict | None:
    return _parse(row, "claimed_criteria", "rp_links")


def get_claim(conn, claim_id: int) -> dict | None:
    return _enrich_claim(
        conn.execute("SELECT * FROM xp_claims WHERE id=?", (claim_id,)).fetchone()
    )


def list_claims_for_character(conn, character_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM xp_claims WHERE character_id=? ORDER BY submitted_at DESC",
        (character_id,)
    ).fetchall()
    return [_enrich_claim(r) for r in rows]


def start_character_review(conn, character_id: int, reviewer_id: str) -> dict | None:
    """Mark a pending character's review as started. Once set, the
    player's sheet edit endpoint refuses to save changes until the
    character is approved or rejected. No-op if already under review
    or already approved/rejected (returns the current row)."""
    char = get_character(conn, character_id)
    if not char:
        return None
    if char.get("is_approved") or char.get("review_started_at"):
        return char
    now = _now()
    conn.execute(
        "UPDATE characters SET review_started_at=?, review_started_by=? WHERE id=?",
        (now, reviewer_id, character_id),
    )
    write_audit(conn, reviewer_id, "start_character_review",
                "character", character_id,
                after={"review_started_at": now})
    return get_character(conn, character_id)


def list_pending_claims(conn) -> list[dict]:
    """Staff: pending claims with character + player info joined."""
    rows = conn.execute("""
        SELECT
            xc.*,
            c.name       AS character_name,
            c.clan       AS character_clan,
            pp.username  AS player_username,
            pd.label     AS period_label
        FROM xp_claims       xc
        JOIN characters      c  ON c.id          = xc.character_id
        JOIN player_profiles pp ON pp.discord_id  = c.discord_id
        LEFT JOIN play_periods pd ON pd.id        = xc.play_period_id
        WHERE xc.status = 'pending'
        ORDER BY xc.submitted_at ASC
    """).fetchall()
    return [_enrich_claim(r) for r in rows]


def list_claims_history(
    conn,
    status: str | None = None,
    period_id: int | None = None,
    character_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """Staff: filterable history of every claim (all statuses).

    Filters are AND-composed; pass None/0/"" to skip a filter. Ordered
    newest-first so the staff sees recent activity at the top.
    """
    where = ["1=1"]
    args: list = []
    if status:
        where.append("xc.status = ?"); args.append(status)
    if period_id:
        where.append("xc.play_period_id = ?"); args.append(period_id)
    if character_id:
        where.append("xc.character_id = ?"); args.append(character_id)
    rows = conn.execute(f"""
        SELECT
            xc.*,
            c.name       AS character_name,
            c.clan       AS character_clan,
            pp.username  AS player_username,
            pd.label     AS period_label
        FROM xp_claims       xc
        JOIN characters      c  ON c.id          = xc.character_id
        JOIN player_profiles pp ON pp.discord_id  = c.discord_id
        LEFT JOIN play_periods pd ON pd.id        = xc.play_period_id
        WHERE {' AND '.join(where)}
        ORDER BY xc.submitted_at DESC
        LIMIT ?
    """, (*args, limit)).fetchall()
    return [_enrich_claim(r) for r in rows]


def create_claim(
    conn,
    character_id: int,
    play_period_id: int,
    claimed_criteria: list[dict],
    rp_links: list[str],
    path: str = "none",
    helper_note: str | None = None,
    staff_claim_conflict: bool = False,
    is_draft: bool = False,
) -> dict:
    """Create an XP claim. Pass is_draft=True to stash an in-progress
    claim that the player can come back to before submitting. Drafts
    never enter the staff queue and don't block re-submission for the
    same period."""
    xp_claimed = sum(c.get("xp_value_at_submission", 0) for c in claimed_criteria)
    status = "draft" if is_draft else "pending"
    cur = conn.execute("""
        INSERT INTO xp_claims
            (character_id, play_period_id, claimed_criteria, rp_links,
             path, helper_note, staff_claim_conflict, xp_claimed,
             status, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        character_id, play_period_id,
        _j(claimed_criteria), _j(rp_links),
        path, helper_note, int(staff_claim_conflict),
        xp_claimed, status, _now(),
    ))
    return get_claim(conn, cur.lastrowid)


def update_draft_claim(
    conn,
    claim_id: int,
    *,
    claimed_criteria: list[dict] | None = None,
    rp_links: list[str] | None = None,
    path: str | None = None,
    helper_note: str | None = None,
    submit_now: bool = False,
) -> dict:
    """Update an existing draft claim in place. Refuses to touch
    non-draft claims so an approved claim can't be silently rewritten.

    Pass submit_now=True to flip the draft into a pending claim
    after applying the updates — that's the "Submit" button path
    from the resumed-draft form."""
    claim = get_claim(conn, claim_id)
    if claim is None:
        raise ValueError(f"Claim {claim_id} not found")
    if claim["status"] != "draft":
        raise ValueError(f"Claim {claim_id} is not a draft (status: {claim['status']})")

    sets: list[str] = []
    params: list = []
    if claimed_criteria is not None:
        sets.append("claimed_criteria=?")
        params.append(_j(claimed_criteria))
        sets.append("xp_claimed=?")
        params.append(sum(c.get("xp_value_at_submission", 0) for c in claimed_criteria))
    if rp_links is not None:
        sets.append("rp_links=?")
        params.append(_j(rp_links))
    if path is not None:
        sets.append("path=?")
        params.append(path)
    if helper_note is not None:
        sets.append("helper_note=?")
        params.append(helper_note or None)
    if submit_now:
        sets.append("status=?")
        params.append("pending")
        sets.append("submitted_at=?")
        params.append(_now())
    if not sets:
        return claim
    params.append(claim_id)
    conn.execute(f"UPDATE xp_claims SET {', '.join(sets)} WHERE id=?", params)
    return get_claim(conn, claim_id)


def discard_draft_claim(conn, claim_id: int) -> None:
    """Delete a draft claim. No-op for non-drafts so we never lose
    an approved or pending submission by accident."""
    claim = get_claim(conn, claim_id)
    if claim is None or claim["status"] != "draft":
        return
    conn.execute("DELETE FROM xp_claims WHERE id=? AND status='draft'", (claim_id,))


def approve_claim(conn, claim_id: int, reviewer_id: str) -> dict:
    claim = get_claim(conn, claim_id)
    if claim is None:
        raise ValueError(f"Claim {claim_id} not found")
    if claim["status"] != "pending":
        raise ValueError(f"Claim {claim_id} is not pending (status: {claim['status']})")

    char = get_character(conn, claim["character_id"])
    if char is None:
        raise ValueError(f"Character {claim['character_id']} not found")

    # Cap XP — never exceed xp_cap
    cap_room = max(0, char["xp_cap"] - char["xp_total"])
    awarded  = min(claim["xp_claimed"], cap_room)

    now = _now()
    conn.execute("""
        UPDATE xp_claims SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?
    """, (reviewer_id, now, claim_id))

    if awarded > 0:
        new_total    = char["xp_total"] + awarded
        retirement   = char.get("retirement_eligible_at")
        if new_total >= char["xp_cap"] and not retirement:
            retirement = now
        conn.execute("""
            UPDATE characters SET xp_total=?, retirement_eligible_at=?, updated_at=?
            WHERE id=?
        """, (new_total, retirement, now, char["id"]))

    conn.execute("""
        INSERT INTO ledger_entries
            (character_id, entry_type, xp_delta, reference_id, reference_type, note, created_by, created_at)
        VALUES (?, 'earn', ?, ?, 'claim', ?, ?, ?)
    """, (char["id"], awarded, claim_id, f"Claim approved — period {claim['play_period_id']}", reviewer_id, now))

    write_audit(conn, reviewer_id, "approve_claim", "claim", claim_id,
                before={"status": "pending"},
                after={"status": "approved", "xp_awarded": awarded})
    enqueue_bot(conn, "claim_approved", {
        "character_id": char["id"],
        "discord_id":   char["discord_id"],
        "claim_id":     claim_id,
        "xp_awarded":   awarded,
        "capped":       awarded < claim["xp_claimed"],
    })
    return get_claim(conn, claim_id)


def reject_claim(conn, claim_id: int, reviewer_id: str, reason: str) -> dict:
    claim = get_claim(conn, claim_id)
    if claim is None:
        raise ValueError(f"Claim {claim_id} not found")
    if claim["status"] != "pending":
        raise ValueError(f"Claim {claim_id} is not pending")

    now = _now()
    conn.execute("""
        UPDATE xp_claims
        SET status='rejected', reviewed_by=?, reviewed_at=?, rejection_reason=?
        WHERE id=?
    """, (reviewer_id, now, reason, claim_id))

    char = get_character(conn, claim["character_id"])
    write_audit(conn, reviewer_id, "reject_claim", "claim", claim_id,
                before={"status": "pending"},
                after={"status": "rejected", "reason": reason})
    enqueue_bot(conn, "claim_rejected", {
        "character_id": char["id"],
        "discord_id":   char["discord_id"],
        "claim_id":     claim_id,
        "reason":       reason,
    })
    return get_claim(conn, claim_id)


# ── Spend Requests ────────────────────────────────────────────────────────────

def _enrich_spend(row: dict | None) -> dict | None:
    return _parse(row, "humanity_conditions")


def get_spend(conn, spend_id: int) -> dict | None:
    return _enrich_spend(
        conn.execute("SELECT * FROM spend_requests WHERE id=?", (spend_id,)).fetchone()
    )


def get_pending_spend_total(conn, character_id: int) -> int:
    """Sum the verified_cost of all pending spend requests for a character.

    Used at submission time to subtract from effective-available XP so a
    player can't queue spends whose combined cost exceeds their balance.
    Pending requests don't deduct xp_spent until approval, so without this
    a 10-XP-available player could submit two 8-XP spends and surprise
    staff at approval time.
    """
    row = conn.execute("""
        SELECT COALESCE(SUM(verified_cost), 0) AS total
        FROM spend_requests
        WHERE character_id=? AND status='pending'
    """, (character_id,)).fetchone()
    return int(row["total"]) if row else 0


def list_spends_for_character(conn, character_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM spend_requests WHERE character_id=? ORDER BY submitted_at DESC",
        (character_id,)
    ).fetchall()
    return [_enrich_spend(r) for r in rows]


def list_pending_spends(conn) -> list[dict]:
    """Staff: pending spends with character + player info joined."""
    rows = conn.execute("""
        SELECT
            sr.*,
            c.name      AS character_name,
            c.clan      AS character_clan,
            pp.username AS player_username
        FROM spend_requests  sr
        JOIN characters      c  ON c.id         = sr.character_id
        JOIN player_profiles pp ON pp.discord_id = c.discord_id
        WHERE sr.status = 'pending'
        ORDER BY sr.submitted_at ASC
    """).fetchall()
    return [_enrich_spend(r) for r in rows]


def list_spends_history(
    conn,
    status: str | None = None,
    character_id: int | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Staff: filterable history of every spend request (all statuses).
    Newest-first ordering."""
    where = ["1=1"]
    args: list = []
    if status:
        where.append("sr.status = ?"); args.append(status)
    if character_id:
        where.append("sr.character_id = ?"); args.append(character_id)
    if category:
        where.append("sr.category = ?"); args.append(category)
    rows = conn.execute(f"""
        SELECT
            sr.*,
            c.name      AS character_name,
            c.clan      AS character_clan,
            pp.username AS player_username
        FROM spend_requests  sr
        JOIN characters      c  ON c.id         = sr.character_id
        JOIN player_profiles pp ON pp.discord_id = c.discord_id
        WHERE {' AND '.join(where)}
        ORDER BY sr.submitted_at DESC
        LIMIT ?
    """, (*args, limit)).fetchall()
    return [_enrich_spend(r) for r in rows]


def create_spend(
    conn,
    character_id: int,
    category: str,
    trait_name: str,
    current_dots: int,
    new_dots: int,
    verified_cost: int,
    is_ingrained: bool = False,
    humanity_conditions: list[str] | None = None,
    note: str | None = None,
    depends_on: int | None = None,
) -> dict:
    """Create a spend request. `depends_on` is an optional parent
    spend_request id — the new spend is held from approval until the
    parent lands. Lets a player batch "Dominate 1→2 then 2→3" without
    staff sequencing in real time."""
    # Reject obviously bogus dependencies (self-loop, wrong character).
    if depends_on is not None:
        parent = get_spend(conn, depends_on)
        if parent is None:
            raise ValueError(f"depends_on spend {depends_on} not found")
        if parent["character_id"] != character_id:
            raise ValueError("depends_on must reference the same character")
    cur = conn.execute("""
        INSERT INTO spend_requests
            (character_id, category, trait_name, current_dots, new_dots,
             verified_cost, is_ingrained, humanity_conditions, note,
             submitted_at, depends_on)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        character_id, category, trait_name, current_dots, new_dots,
        verified_cost, int(is_ingrained),
        _j(humanity_conditions) if humanity_conditions else None,
        note, _now(), depends_on,
    ))
    return get_spend(conn, cur.lastrowid)


def _apply_spend_to_sheet(sheet: dict, category: str, trait_name: str, new_dots: int) -> dict:
    """Mutate `sheet` (in place + return) so the trait the player just had
    approved is reflected on their sheet. Idempotent — if the trait already
    exists at the same dots, this is a no-op.

    Maps the spend's (category, trait_name, new_dots) onto the right
    sheet_json slot:
      Attribute / Skill / New Skill           -> attr_* / sk_* int
      Clan / Other / Caitiff / Ingrained Disc -> disc_* int
      Blood Sorcery Ritual                    -> rituals[]    {name, level}
      Thin-Blood Alchemy Formula              -> formulae[]   {name, level}
      Advantage                               -> advantages[] {name, dots}
      Specialty                               -> specialties[]{skill, name}
      Blood Potency / Humanity                -> top-level int
    Anything we can't map (e.g. arbitrary merit names with no clear key)
    falls through to advantages[] as a {name, dots} entry — the player can
    rename on their sheet but the spend is reflected.
    """
    from .v5_traits import (
        V5_ATTRIBUTES as _ATTRS,
        V5_SKILLS     as _SKILLS,
        V5_DISCIPLINES as _DISCS,
    )
    cat = (category or "").strip().lower()
    label = (trait_name or "").strip()

    def _label_to_key(label: str, pool) -> str | None:
        """Look up a sheet key by human label, case-insensitive."""
        lab = label.casefold()
        for key, l in pool:
            if l.casefold() == lab:
                return key
        return None

    def _upsert_list(list_key: str, dots_field: str, dots: int, name: str) -> None:
        items = list(sheet.get(list_key) or [])
        nm = name.casefold()
        for i, it in enumerate(items):
            if isinstance(it, dict) and str(it.get("name", "")).casefold() == nm:
                items[i] = {**it, "name": name, dots_field: dots}
                break
        else:
            items.append({"name": name, dots_field: dots})
        sheet[list_key] = items

    # ── Attributes ──
    if cat == "attribute":
        flat = [(k, l) for _, group in _ATTRS for k, l in group]
        key = _label_to_key(label, flat)
        if key:
            sheet[key] = max(int(sheet.get(key, 0)), int(new_dots))
        return sheet

    # ── Skills (existing or new) ──
    if cat in ("skill", "new skill"):
        flat = [(k, l) for _, group in _SKILLS for k, l in group]
        key = _label_to_key(label, flat)
        if key:
            sheet[key] = max(int(sheet.get(key, 0)), int(new_dots))
        return sheet

    # ── Specialty ──
    if cat == "specialty":
        items = list(sheet.get("specialties") or [])
        # Format: "Brawl: Grappling" — split if present.
        skill_label, _, spec_name = label.partition(":")
        spec_name = spec_name.strip() or label
        flat = [(k, l) for _, group in _SKILLS for k, l in group]
        skill_key = _label_to_key(skill_label.strip(), flat) or ""
        items.append({"skill": skill_key, "name": spec_name})
        sheet["specialties"] = items
        return sheet

    # ── Disciplines (all flavours) ──
    if "discipline" in cat:
        key = _label_to_key(label, _DISCS)
        if key:
            sheet[key] = max(int(sheet.get(key, 0)), int(new_dots))
        return sheet

    # ── Ritual / Formula ──
    if cat == "blood sorcery ritual":
        _upsert_list("rituals", "level", int(new_dots), label)
        return sheet
    if cat == "thin-blood alchemy formula":
        _upsert_list("formulae", "level", int(new_dots), label)
        return sheet

    # ── Blood Potency / Humanity ──
    if cat == "blood potency":
        sheet["blood_potency"] = max(int(sheet.get("blood_potency", 0)), int(new_dots))
        return sheet
    if cat == "humanity":
        sheet["humanity"] = max(int(sheet.get("humanity", 0)), int(new_dots))
        return sheet

    # ── Advantage (Merit / Background / catch-all) ──
    if cat == "advantage":
        _upsert_list("advantages", "dots", int(new_dots), label)
        return sheet

    # Fallback — drop into advantages[] so the spend is still visible.
    _upsert_list("advantages", "dots", int(new_dots), label)
    return sheet


def approve_spend(conn, spend_id: int, reviewer_id: str) -> dict:
    spend = get_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Spend {spend_id} not found")
    if spend["status"] != "pending":
        raise ValueError(f"Spend {spend_id} is not pending (status: {spend['status']})")

    # Dependency gate (migration 023). If this spend was submitted as
    # "depends on" another, refuse to approve until that parent lands.
    # Parents in 'rejected' state are also fatal — they should have
    # cascade-rejected this row already, but guard defensively anyway.
    if spend.get("depends_on"):
        parent = get_spend(conn, spend["depends_on"])
        if parent is None:
            raise ValueError(
                f"Parent spend {spend['depends_on']} missing — refusing to approve"
            )
        if parent["status"] == "pending":
            raise ValueError(
                f"Parent spend #{parent['id']} is still pending — approve it first"
            )
        if parent["status"] == "rejected":
            raise ValueError(
                f"Parent spend #{parent['id']} was rejected — this dependent cannot be approved"
            )

    char = get_character(conn, spend["character_id"])
    if char is None:
        raise ValueError(f"Character {spend['character_id']} not found")

    available = char["xp_available"]
    if available < spend["verified_cost"]:
        raise ValueError(
            f"Insufficient XP — {available} available, {spend['verified_cost']} required"
        )

    if spend["is_ingrained"]:
        new_used = char["ingrained_xp_used"] + spend["verified_cost"]
        if new_used > _INGRAINED_XP_CAP:
            raise ValueError(
                f"Ingrained Discipline budget exceeded "
                f"({char['ingrained_xp_used']} + {spend['verified_cost']} > {_INGRAINED_XP_CAP})"
            )

    now = _now()
    conn.execute("""
        UPDATE spend_requests SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?
    """, (reviewer_id, now, spend_id))

    # Deduct XP from character
    new_spent = char["xp_spent"] + spend["verified_cost"]
    ingrained_used = (
        char["ingrained_xp_used"] + spend["verified_cost"]
        if spend["is_ingrained"] else char["ingrained_xp_used"]
    )
    conn.execute("""
        UPDATE characters SET xp_spent=?, ingrained_xp_used=?, updated_at=? WHERE id=?
    """, (new_spent, ingrained_used, now, char["id"]))

    # ── Auto-apply trait bump to the character sheet ──
    # Players were finding it confusing to have spends approved but their
    # sheet still showed the old dots — they had to ask staff to manually
    # bump it. Now the approval handler does it for them, idempotently.
    sheet_before = dict(char.get("sheet_json") or {})
    sheet_after  = _apply_spend_to_sheet(
        dict(sheet_before),
        category=spend["category"],
        trait_name=spend["trait_name"],
        new_dots=spend["new_dots"],
    )
    if sheet_after != sheet_before:
        conn.execute(
            "UPDATE characters SET sheet_json=?, updated_at=? WHERE id=?",
            (_j(sheet_after), now, char["id"]),
        )

    # Ledger
    conn.execute("""
        INSERT INTO ledger_entries
            (character_id, entry_type, xp_delta, reference_id, reference_type, note, created_by, created_at)
        VALUES (?, 'spend', ?, ?, 'spend', ?, ?, ?)
    """, (
        char["id"], -spend["verified_cost"], spend_id,
        f"{spend['category']}: {spend['trait_name']} "
        f"{spend['current_dots']}→{spend['new_dots']}",
        reviewer_id, now,
    ))

    write_audit(conn, reviewer_id, "approve_spend", "spend", spend_id,
                before={"status": "pending"},
                after={"status": "approved"})
    enqueue_bot(conn, "spend_approved", {
        "character_id": char["id"],
        "discord_id":   char["discord_id"],
        "spend_id":     spend_id,
        "trait_name":   spend["trait_name"],
        "xp_cost":      spend["verified_cost"],
    })
    return get_spend(conn, spend_id)


def reject_spend(conn, spend_id: int, reviewer_id: str, reason: str) -> dict:
    spend = get_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Spend {spend_id} not found")
    if spend["status"] != "pending":
        raise ValueError(f"Spend {spend_id} is not pending")

    now = _now()
    conn.execute("""
        UPDATE spend_requests
        SET status='rejected', reviewed_by=?, reviewed_at=?, rejection_reason=?
        WHERE id=?
    """, (reviewer_id, now, reason, spend_id))

    char = get_character(conn, spend["character_id"])
    write_audit(conn, reviewer_id, "reject_spend", "spend", spend_id,
                before={"status": "pending"},
                after={"status": "rejected", "reason": reason})
    enqueue_bot(conn, "spend_rejected", {
        "character_id": char["id"],
        "discord_id":   char["discord_id"],
        "spend_id":     spend_id,
        "reason":       reason,
    })

    # Cascade — any pending spends that depended on this one can never
    # be approved, so mark them rejected now with a clear reason. Loop
    # because chains can be deeper than one level.
    cascade_reason = f"Parent spend #{spend_id} was rejected: {reason}"
    dependents = conn.execute(
        "SELECT id FROM spend_requests "
        "WHERE depends_on=? AND status='pending'",
        (spend_id,),
    ).fetchall()
    for dep in dependents:
        # Recurse so dependents-of-dependents also cascade.
        reject_spend(conn, dep["id"], reviewer_id, cascade_reason)

    return get_spend(conn, spend_id)


# ── Ledger ────────────────────────────────────────────────────────────────────

def get_ledger(conn, character_id: int, limit: int = 50) -> list[dict]:
    return conn.execute("""
        SELECT * FROM ledger_entries WHERE character_id=?
        ORDER BY created_at DESC LIMIT ?
    """, (character_id, limit)).fetchall()


def append_ledger_entry(
    conn,
    character_id: int,
    entry_type: str,
    xp_delta: int,
    created_by: str,
    reference_id: int | None = None,
    reference_type: str | None = None,
    note: str | None = None,
) -> dict:
    now = _now()
    cur = conn.execute("""
        INSERT INTO ledger_entries
            (character_id, entry_type, xp_delta, reference_id, reference_type, note, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (character_id, entry_type, xp_delta, reference_id, reference_type, note, created_by, now))
    return conn.execute(
        "SELECT * FROM ledger_entries WHERE id=?", (cur.lastrowid,)
    ).fetchone()


# ── Audit Log ─────────────────────────────────────────────────────────────────

def write_audit(
    conn,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    conn.execute("""
        INSERT INTO audit_log
            (actor_id, action, target_type, target_id, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        actor_id, action, target_type, target_id,
        _j(before) if before else None,
        _j(after)  if after  else None,
        _now(),
    ))


def list_audit(
    conn,
    target_type: str | None = None,
    target_id: int | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses, params = [], []
    if target_type:
        clauses.append("a.target_type=?")
        params.append(target_type)
    if target_id is not None:
        clauses.append("a.target_id=?")
        params.append(target_id)
    if actor_id:
        clauses.append("a.actor_id=?")
        params.append(actor_id)
    if action:
        clauses.append("a.action=?")
        params.append(action)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    return conn.execute(f"""
        SELECT a.*,
               pp.username    AS actor_username,
               pp.staff_role  AS actor_staff_role
        FROM audit_log a
        LEFT JOIN player_profiles pp ON pp.discord_id = a.actor_id
        {where}
        ORDER BY a.created_at DESC
        LIMIT ?
    """, params).fetchall()


# ── Coteries ──────────────────────────────────────────────────────────────────

def get_coterie(conn, coterie_id: int) -> dict | None:
    return conn.execute("SELECT * FROM coteries WHERE id=?", (coterie_id,)).fetchone()


def get_coterie_for_character(conn, character_id: int) -> dict | None:
    return conn.execute("""
        SELECT co.* FROM coteries co
        JOIN coterie_memberships cm ON cm.coterie_id = co.id
        WHERE cm.character_id=? AND co.status='active'
        LIMIT 1
    """, (character_id,)).fetchone()


def list_coteries(conn, status: str = "active") -> list[dict]:
    return conn.execute("""
        SELECT co.*, COUNT(cm.id) AS member_count
        FROM coteries co
        LEFT JOIN coterie_memberships cm ON cm.coterie_id = co.id
        WHERE co.status=?
        GROUP BY co.id
        ORDER BY co.name
    """, (status,)).fetchall()


def create_coterie(
    conn,
    name: str,
    chasse: int = 1,
    lien: int = 0,
    portillon: int = 0,
    discord_role_id: str | None = None,
) -> dict:
    now = _now()
    cur = conn.execute("""
        INSERT INTO coteries (name, chasse, lien, portillon, discord_role_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, chasse, lien, portillon, discord_role_id, now, now))
    return get_coterie(conn, cur.lastrowid)


def list_coterie_members(conn, coterie_id: int) -> list[dict]:
    return conn.execute("""
        SELECT cm.*, c.name AS character_name, c.clan AS character_clan,
               pp.username AS player_username
        FROM coterie_memberships cm
        JOIN characters      c  ON c.id         = cm.character_id
        JOIN player_profiles pp ON pp.discord_id = c.discord_id
        WHERE cm.coterie_id=?
        ORDER BY cm.role DESC, c.name
    """, (coterie_id,)).fetchall()


def add_coterie_member(conn, coterie_id: int, character_id: int, role: str = "member") -> dict:
    # Re-adding an existing member (role change) doesn't count toward the cap.
    already = conn.execute(
        "SELECT 1 FROM coterie_memberships WHERE coterie_id=? AND character_id=?",
        (coterie_id, character_id),
    ).fetchone()
    if not already:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM coterie_memberships WHERE coterie_id=?",
            (coterie_id,),
        ).fetchone()["n"]
        if count >= settings.COTERIE_MAX_MEMBERS:
            raise ValueError(
                f"Coterie is full — max {settings.COTERIE_MAX_MEMBERS} members."
            )
    now = _now()
    conn.execute("""
        INSERT INTO coterie_memberships (coterie_id, character_id, role, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(coterie_id, character_id) DO UPDATE SET role=excluded.role
    """, (coterie_id, character_id, role, now))
    conn.execute("UPDATE coteries SET updated_at=? WHERE id=?", (now, coterie_id))
    return get_coterie(conn, coterie_id)


# ── Coterie merits + flaws ─────────────────────────────────────────────────────

def list_coterie_merits(conn, coterie_id: int) -> list[dict]:
    """Coterie merits joined with the member character's name."""
    return conn.execute("""
        SELECT cm.*, c.name AS character_name
        FROM coterie_merits cm
        JOIN characters c ON c.id = cm.character_id
        WHERE cm.coterie_id = ?
        ORDER BY cm.created_at DESC
    """, (coterie_id,)).fetchall()


def add_coterie_merit(
    conn,
    coterie_id: int,
    character_id: int,
    merit_name: str,
    dots: int,
    merit_type: str = "purchased",
    actor_id: str = "system",
) -> dict:
    cur = conn.execute("""
        INSERT INTO coterie_merits
            (coterie_id, character_id, merit_name, dots, merit_type)
        VALUES (?, ?, ?, ?, ?)
    """, (coterie_id, character_id, merit_name, dots, merit_type))
    row = conn.execute("SELECT * FROM coterie_merits WHERE id=?",
                       (cur.lastrowid,)).fetchone()
    write_audit(conn, actor_id, "add_coterie_merit", "coterie", coterie_id,
                after={"merit": merit_name, "dots": dots, "character_id": character_id})
    return row


def remove_coterie_merit(conn, merit_id: int, actor_id: str = "system") -> None:
    row = conn.execute("SELECT * FROM coterie_merits WHERE id=?", (merit_id,)).fetchone()
    if not row:
        return
    conn.execute("DELETE FROM coterie_merits WHERE id=?", (merit_id,))
    write_audit(conn, actor_id, "remove_coterie_merit", "coterie", row["coterie_id"],
                before={"merit": row["merit_name"], "dots": row["dots"]})


def list_coterie_flaws(conn, coterie_id: int) -> list[dict]:
    return conn.execute("""
        SELECT * FROM coterie_flaws
        WHERE coterie_id = ?
        ORDER BY created_at DESC
    """, (coterie_id,)).fetchall()


def add_coterie_flaw(
    conn,
    coterie_id: int,
    flaw_name: str,
    dots: int,
    creation_grant: int = 0,
    actor_id: str = "system",
) -> dict:
    cur = conn.execute("""
        INSERT INTO coterie_flaws
            (coterie_id, flaw_name, dots, creation_grant)
        VALUES (?, ?, ?, ?)
    """, (coterie_id, flaw_name, dots, creation_grant))
    row = conn.execute("SELECT * FROM coterie_flaws WHERE id=?",
                       (cur.lastrowid,)).fetchone()
    write_audit(conn, actor_id, "add_coterie_flaw", "coterie", coterie_id,
                after={"flaw": flaw_name, "dots": dots, "grant": creation_grant})
    return row


def remove_coterie_flaw(conn, flaw_id: int, actor_id: str = "system") -> None:
    row = conn.execute("SELECT * FROM coterie_flaws WHERE id=?", (flaw_id,)).fetchone()
    if not row:
        return
    conn.execute("DELETE FROM coterie_flaws WHERE id=?", (flaw_id,))
    write_audit(conn, actor_id, "remove_coterie_flaw", "coterie", row["coterie_id"],
                before={"flaw": row["flaw_name"], "dots": row["dots"]})


def remove_coterie_member(conn, coterie_id: int, character_id: int) -> None:
    conn.execute(
        "DELETE FROM coterie_memberships WHERE coterie_id=? AND character_id=?",
        (coterie_id, character_id)
    )
    # Flip the member's contributions to 'removed' so they no longer count
    # toward the coterie's effective rating, then recompute the cached
    # chasse/lien/portillon columns. Donations also revert: the sheet flag
    # gets cleared so the player gets their trait back un-shared.
    remove_member_contributions(conn, coterie_id, character_id, reason="left coterie")
    _recompute_coterie_ratings(conn, coterie_id)
    conn.execute("UPDATE coteries SET updated_at=? WHERE id=?", (_now(), coterie_id))


# ── Coterie contributions (unified accounting) ───────────────────────────────
#
# Every dot on a coterie sheet is tracked here with its provenance, so we
# can suspend a contributor's dots when they go inactive (and reactivate
# when they come back) and so the Steward can see at a glance "who paid
# for what". The flat `coteries.chasse/lien/portillon` columns are kept
# as a denormalized cache so existing read paths don't have to JOIN here.

CONTRIBUTION_TYPES = {
    "creation_free", "flaw_bonus", "paid_xp", "donated",
    "timeskip_advance", "staff_grant",
}
CONTRIBUTION_TARGET_KINDS = {
    "chasse", "lien", "portillon", "merit", "background", "flaw",
}
CONTRIBUTION_STATUSES = {"active", "suspended", "removed"}

# Per Steward direction (2026-05): every named coterie merit/background
# caps at 3 dots total across all contributors. C/L/P cap at 5.
COTERIE_NAMED_TRAIT_CAP    = 3
COTERIE_DOMAIN_CAP         = 5


def list_coterie_contributions(
    conn,
    coterie_id: int,
    *,
    target_kind: str | None = None,
    target_name: str | None = None,
    status: str | None = "active",
    include_character: bool = True,
) -> list[dict]:
    """List contributions for a coterie. Defaults to active-only (which
    is what every UI display wants); pass status=None to see everything
    including suspended/removed rows for audit views."""
    clauses, params = ["cc.coterie_id=?"], [coterie_id]
    if target_kind:
        clauses.append("cc.target_kind=?"); params.append(target_kind)
    if target_name is not None:
        clauses.append("cc.target_name=?"); params.append(target_name)
    if status:
        clauses.append("cc.status=?"); params.append(status)
    join = ""
    cols = "cc.*"
    if include_character:
        join = "LEFT JOIN characters c ON c.id = cc.character_id"
        cols = "cc.*, c.name AS character_name, c.clan AS character_clan"
    return conn.execute(
        f"SELECT {cols} FROM coterie_contributions cc {join} "
        f"WHERE {' AND '.join(clauses)} ORDER BY cc.created_at DESC",
        params,
    ).fetchall()


def coterie_effective_rating(
    conn,
    coterie_id: int,
    target_kind: str,
    target_name: str | None = None,
) -> int:
    """Sum of active contributions for the given trait. For ratings
    (chasse/lien/portillon) pass target_name=None; for merits/backgrounds
    pass the named item."""
    q = ("SELECT COALESCE(SUM(dots), 0) AS n FROM coterie_contributions "
         "WHERE coterie_id=? AND target_kind=? AND status='active'")
    p: list = [coterie_id, target_kind]
    if target_name is not None:
        q += " AND target_name=?"; p.append(target_name)
    return conn.execute(q, p).fetchone()["n"]


def _recompute_coterie_ratings(conn, coterie_id: int) -> None:
    """Refresh the cached `coteries.chasse/lien/portillon` columns from
    the active contributions. Call after every mutation that could
    change a rating (add/suspend/unsuspend/remove)."""
    chasse    = coterie_effective_rating(conn, coterie_id, "chasse")
    lien      = coterie_effective_rating(conn, coterie_id, "lien")
    portillon = coterie_effective_rating(conn, coterie_id, "portillon")
    # Domain cap defensively — a contribution that was active when added
    # might exceed 5 if multiple suspended members reactivate; we expose
    # the truth in the contributions table but cap the cache.
    conn.execute(
        "UPDATE coteries SET chasse=?, lien=?, portillon=?, updated_at=? WHERE id=?",
        (min(COTERIE_DOMAIN_CAP, chasse),
         min(COTERIE_DOMAIN_CAP, lien),
         min(COTERIE_DOMAIN_CAP, portillon),
         _now(), coterie_id),
    )


def add_coterie_contribution(
    conn,
    *,
    coterie_id: int,
    contribution_type: str,
    target_kind: str,
    target_name: str | None,
    dots: int,
    character_id: int | None = None,
    xp_paid: int = 0,
    period_id: int | None = None,
    spend_id: int | None = None,
    note: str | None = None,
    status: str = "active",
    recompute: bool = True,
) -> dict:
    """Direct insert of a contribution row. Validates the enums up front
    so callers don't accidentally write garbage that breaks the audit
    view. Set recompute=False when batch-inserting and call
    _recompute_coterie_ratings() once at the end."""
    if contribution_type not in CONTRIBUTION_TYPES:
        raise ValueError(f"Unknown contribution_type: {contribution_type!r}")
    if target_kind not in CONTRIBUTION_TARGET_KINDS:
        raise ValueError(f"Unknown target_kind: {target_kind!r}")
    if status not in CONTRIBUTION_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    if dots < 1:
        raise ValueError(f"dots must be >= 1, got {dots}")
    now = _now()
    cur = conn.execute("""
        INSERT INTO coterie_contributions
            (coterie_id, character_id, contribution_type, target_kind,
             target_name, dots, status, xp_paid, period_id, spend_id,
             note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        coterie_id, character_id, contribution_type, target_kind,
        target_name, int(dots), status, int(xp_paid), period_id,
        spend_id, note, now, now,
    ))
    row = conn.execute(
        "SELECT * FROM coterie_contributions WHERE id=?",
        (cur.lastrowid,),
    ).fetchone()
    if recompute and status == "active" and target_kind in ("chasse", "lien", "portillon"):
        _recompute_coterie_ratings(conn, coterie_id)
    return row


def set_contribution_status(
    conn,
    contribution_id: int,
    new_status: str,
    actor_id: str = "system",
) -> dict | None:
    """Flip a single contribution's status. Used by manual staff edits.
    Bulk operations (suspend on inactive, remove on leave) use the
    targeted helpers below."""
    if new_status not in CONTRIBUTION_STATUSES:
        raise ValueError(f"Unknown status: {new_status!r}")
    row = conn.execute(
        "SELECT * FROM coterie_contributions WHERE id=?",
        (contribution_id,),
    ).fetchone()
    if not row:
        return None
    if row["status"] == new_status:
        return row
    conn.execute(
        "UPDATE coterie_contributions SET status=?, updated_at=? WHERE id=?",
        (new_status, _now(), contribution_id),
    )
    if row["target_kind"] in ("chasse", "lien", "portillon"):
        _recompute_coterie_ratings(conn, row["coterie_id"])
    write_audit(conn, actor_id, "set_contribution_status",
                "coterie_contribution", contribution_id,
                before={"status": row["status"]},
                after={"status": new_status})
    return conn.execute(
        "SELECT * FROM coterie_contributions WHERE id=?",
        (contribution_id,),
    ).fetchone()


def suspend_member_contributions(
    conn,
    character_id: int,
    actor_id: str = "system",
) -> list[int]:
    """Flip every active contribution this character made to 'suspended'.
    Triggered by the inactivity sweep / Mark Inactive button. Returns the
    list of affected coterie_ids so callers can recompute their cached
    ratings (this fn does that automatically)."""
    rows = conn.execute(
        "SELECT id, coterie_id, target_kind FROM coterie_contributions "
        "WHERE character_id=? AND status='active'",
        (character_id,),
    ).fetchall()
    affected: set[int] = set()
    for r in rows:
        conn.execute(
            "UPDATE coterie_contributions SET status='suspended', updated_at=? WHERE id=?",
            (_now(), r["id"]),
        )
        affected.add(r["coterie_id"])
    for cid in affected:
        _recompute_coterie_ratings(conn, cid)
    if rows:
        write_audit(conn, actor_id, "suspend_member_contributions",
                    "character", character_id,
                    after={"count": len(rows),
                           "coteries": sorted(affected)})
    return sorted(affected)


def unsuspend_member_contributions(
    conn,
    character_id: int,
    actor_id: str = "system",
) -> list[int]:
    """Reverse of suspend_member_contributions — used when an inactive
    character comes back to active duty."""
    rows = conn.execute(
        "SELECT id, coterie_id FROM coterie_contributions "
        "WHERE character_id=? AND status='suspended'",
        (character_id,),
    ).fetchall()
    affected: set[int] = set()
    for r in rows:
        conn.execute(
            "UPDATE coterie_contributions SET status='active', updated_at=? WHERE id=?",
            (_now(), r["id"]),
        )
        affected.add(r["coterie_id"])
    for cid in affected:
        _recompute_coterie_ratings(conn, cid)
    if rows:
        write_audit(conn, actor_id, "unsuspend_member_contributions",
                    "character", character_id,
                    after={"count": len(rows),
                           "coteries": sorted(affected)})
    return sorted(affected)


def remove_member_contributions(
    conn,
    coterie_id: int,
    character_id: int,
    *,
    reason: str = "left coterie",
    actor_id: str = "system",
) -> list[dict]:
    """Permanently retire a member's contributions to one coterie. Used
    by remove_coterie_member. Donations (contribution_type='donated') get
    their sheet flag cleared as a side effect so the trait reverts to the
    player un-shared."""
    rows = conn.execute(
        "SELECT * FROM coterie_contributions "
        "WHERE coterie_id=? AND character_id=? AND status != 'removed'",
        (coterie_id, character_id),
    ).fetchall()
    if not rows:
        return []
    for r in rows:
        conn.execute(
            "UPDATE coterie_contributions SET status='removed', updated_at=?, "
            "note = COALESCE(note, '') || ' | removed: ' || ? WHERE id=?",
            (_now(), reason, r["id"]),
        )
        if r["contribution_type"] == "donated" and r["target_name"]:
            # Clear the sheet flag for this donation — the player's
            # advantages[] entry stops being "shared with coterie".
            clear_donation_share_on_sheet(
                conn, character_id, coterie_id,
                target_kind=r["target_kind"],
                target_name=r["target_name"],
            )
    # Domain caches need refresh whether or not any C/L/P contributions
    # were affected — cheap enough to always recompute on removal.
    _recompute_coterie_ratings(conn, coterie_id)
    return rows


# ── Donation share flag (mutates character sheet_json) ──────────────────────

def add_donation_share_to_sheet(
    conn,
    character_id: int,
    coterie_id: int,
    target_kind: str,
    target_name: str,
) -> None:
    """Mark the player's existing merit/background entry as shared with
    the coterie. Doesn't remove the entry — the donation rules say the
    trait shows on BOTH the character sheet and the coterie.

    Target kind 'merit' lives under sheet_json['merits'][] or
    sheet_json['advantages'][] (per chronicle config); we check both.
    'background' lives under sheet_json['backgrounds'][] or also under
    'advantages' for legacy chars."""
    char = get_character(conn, character_id)
    if not char:
        return
    sheet = dict(char.get("sheet_json") or {})
    candidates = ["advantages", "merits", "backgrounds"]
    nm = target_name.casefold()
    mutated = False
    for list_key in candidates:
        items = list(sheet.get(list_key) or [])
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            if str(it.get("name", "")).casefold() != nm:
                continue
            existing = it.get("shared_with_coteries") or []
            if coterie_id in existing:
                continue
            items[i] = {**it, "shared_with_coteries": existing + [coterie_id]}
            sheet[list_key] = items
            mutated = True
    if mutated:
        conn.execute(
            "UPDATE characters SET sheet_json=?, updated_at=? WHERE id=?",
            (_j(sheet), _now(), character_id),
        )


def clear_donation_share_on_sheet(
    conn,
    character_id: int,
    coterie_id: int,
    target_kind: str,
    target_name: str,
) -> None:
    """Mirror of add_donation_share_to_sheet — removes coterie_id from
    the shared_with_coteries list on the matching trait. Leaves the
    entry in place (the player still owns the dots)."""
    char = get_character(conn, character_id)
    if not char:
        return
    sheet = dict(char.get("sheet_json") or {})
    candidates = ["advantages", "merits", "backgrounds"]
    nm = target_name.casefold()
    mutated = False
    for list_key in candidates:
        items = list(sheet.get(list_key) or [])
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            if str(it.get("name", "")).casefold() != nm:
                continue
            existing = list(it.get("shared_with_coteries") or [])
            if coterie_id not in existing:
                continue
            existing.remove(coterie_id)
            new_entry = {**it}
            if existing:
                new_entry["shared_with_coteries"] = existing
            else:
                new_entry.pop("shared_with_coteries", None)
            items[i] = new_entry
            sheet[list_key] = items
            mutated = True
    if mutated:
        conn.execute(
            "UPDATE characters SET sheet_json=?, updated_at=? WHERE id=?",
            (_j(sheet), _now(), character_id),
        )


# ── Soft duplicate check for time-skip advance ───────────────────────────────

def recent_coterie_advance_in_period(
    conn,
    coterie_id: int,
    target_kind: str,
    period_id: int,
) -> dict | None:
    """Return the most-recent advance spend in this period for the given
    rating, if any. Used to soft-warn staff during approval ("you already
    bumped Chasse this period, sure?")."""
    return conn.execute("""
        SELECT * FROM coterie_spends
        WHERE coterie_id=? AND period_id=? AND trait_name=?
          AND contribution_type='timeskip_advance'
          AND status IN ('pending', 'funded', 'approved')
        ORDER BY submitted_at DESC LIMIT 1
    """, (coterie_id, period_id, target_kind)).fetchone()


# ── Single-funder spend variants ─────────────────────────────────────────────

def create_coterie_single_funder_spend(
    conn,
    *,
    coterie_id: int,
    funded_by_character_id: int,
    contribution_type: str,
    target_kind: str,
    target_name: str | None,
    current_dots: int = 0,
    new_dots: int = 0,
    xp_cost: int = 0,
    period_id: int | None = None,
    justification: str | None = None,
) -> dict:
    """Create a coterie_spend where ONE member funds the whole cost
    (advance / personal-XP merit / donation). Status starts at 'pending'
    if there's an XP cost (so we can verify the funder has the XP), or
    skips straight to 'funded' for zero-cost donations.

    The contribution row is NOT written until staff approval — this
    matches the pattern of the existing equal-split flow."""
    if contribution_type not in CONTRIBUTION_TYPES:
        raise ValueError(f"Unknown contribution_type: {contribution_type!r}")
    if target_kind not in CONTRIBUTION_TARGET_KINDS:
        raise ValueError(f"Unknown target_kind: {target_kind!r}")

    # Verify funder is a member and (for paid_xp) has enough XP.
    members = list_coterie_members(conn, coterie_id)
    if not any(m["character_id"] == funded_by_character_id for m in members):
        raise ValueError("Funding character is not a member of this coterie.")
    if xp_cost > 0:
        char = get_character(conn, funded_by_character_id)
        if char is None:
            raise ValueError("Funding character not found.")
        if (char.get("xp_available") or 0) < xp_cost:
            raise ValueError(
                f"{char['name']} only has {char.get('xp_available') or 0} XP available "
                f"(needs {xp_cost})."
            )

    # Spend_category is the legacy enum — mirror to closest match so
    # existing list/filter code (which looks at spend_category) keeps
    # working. The richer contribution_type is the new source of truth.
    if target_kind in ("chasse", "lien", "portillon"):
        spend_category = "domain"
    elif target_kind == "merit":
        spend_category = "merit"
    else:
        spend_category = "other"

    # Funded immediately when there's no XP cost (donations) — the funder
    # has nothing to commit. With cost > 0, status starts 'funded' too
    # because there's only ONE funder and they've implicitly committed by
    # creating the spend; staff approval will deduct the XP. This skips
    # the legacy per-member commit cycle entirely.
    initial_status = "funded"

    # Stash the funder in `contributions` JSON so legacy code paths
    # (list_coterie_spends / approve_coterie_spend / commit_all) read
    # the same shape they always have, AND set funded_by_character_id
    # as the canonical pointer.
    contributions = {str(funded_by_character_id): xp_cost}

    now = _now()
    cur = conn.execute("""
        INSERT INTO coterie_spends
            (coterie_id, trait_name, current_dots, new_dots,
             total_cost, per_member_cost, contributions,
             status, spend_category, initiated_by, justification,
             submitted_at, funded_by_character_id, contribution_type,
             period_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        coterie_id,
        target_name or target_kind,  # trait_name for legacy display
        int(current_dots), int(new_dots),
        int(xp_cost), int(xp_cost),
        _j(contributions),
        initial_status, spend_category,
        str(funded_by_character_id), justification, now,
        funded_by_character_id, contribution_type, period_id,
    ))
    return get_coterie_spend(conn, cur.lastrowid)


# ── Validators ───────────────────────────────────────────────────────────────

def validate_coterie_advance(
    conn,
    coterie_id: int,
    target_kind: str,
    delta: int = 1,
) -> tuple[bool, str | None]:
    """Pre-flight check for a +N C/L/P advance. Returns (ok, error_msg).
    Doesn't enforce the per-period rate-limit — that's a soft warning
    surfaced separately so staff can override."""
    if target_kind not in ("chasse", "lien", "portillon"):
        return False, f"{target_kind} isn't a coterie rating."
    if delta < 1:
        return False, "Advance delta must be >= 1."
    current = coterie_effective_rating(conn, coterie_id, target_kind)
    if current + delta > COTERIE_DOMAIN_CAP:
        return False, f"{target_kind.title()} would exceed the {COTERIE_DOMAIN_CAP}-dot cap (currently {current})."
    if target_kind == "portillon":
        chasse = coterie_effective_rating(conn, coterie_id, "chasse")
        if current + delta > chasse:
            return False, (f"Portillon cannot exceed Chasse "
                           f"(would be {current + delta} vs Chasse {chasse}).")
    return True, None


def validate_coterie_named_trait(
    conn,
    coterie_id: int,
    target_kind: str,
    target_name: str,
    delta: int,
) -> tuple[bool, str | None]:
    """Pre-flight check for a +N coterie merit/background dot. The
    Steward's house rule caps every named coterie trait at 3 dots TOTAL
    across all contributors, so Haven 3 is the absolute ceiling whether
    one member bought all 3 or three members chipped in one each."""
    if target_kind not in ("merit", "background"):
        return False, f"Use validate_coterie_advance for {target_kind}."
    if delta < 1:
        return False, "Delta must be >= 1."
    if not target_name or not target_name.strip():
        return False, "Trait name is required."
    current = coterie_effective_rating(conn, coterie_id, target_kind, target_name)
    if current + delta > COTERIE_NAMED_TRAIT_CAP:
        return False, (f"\"{target_name}\" would exceed the "
                       f"{COTERIE_NAMED_TRAIT_CAP}-dot cap (currently {current}).")
    return True, None


def get_coterie_request(conn, request_id: int) -> dict | None:
    return _parse(
        conn.execute("SELECT * FROM coterie_requests WHERE id=?", (request_id,)).fetchone(),
        "member_ids"
    )


def list_pending_coterie_requests(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM coterie_requests WHERE status='pending' ORDER BY submitted_at ASC"
    ).fetchall()
    return [_parse(r, "member_ids") for r in rows]


def create_coterie_request(
    conn,
    requested_by: str,
    proposed_name: str,
    member_ids: list[int],
    note: str | None = None,
) -> dict:
    cur = conn.execute("""
        INSERT INTO coterie_requests (requested_by, proposed_name, member_ids, note, submitted_at)
        VALUES (?, ?, ?, ?, ?)
    """, (requested_by, proposed_name, _j(member_ids), note, _now()))
    return get_coterie_request(conn, cur.lastrowid)


def approve_coterie_request(conn, request_id: int, reviewer_id: str) -> dict:
    req = get_coterie_request(conn, request_id)
    if req is None:
        raise ValueError(f"Coterie request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is not pending")

    member_ids = req["member_ids"] or []
    if len(member_ids) > settings.COTERIE_MAX_MEMBERS:
        raise ValueError(
            f"Cannot approve — request has {len(member_ids)} members "
            f"(max {settings.COTERIE_MAX_MEMBERS})."
        )

    coterie = create_coterie(conn, req["proposed_name"])
    for char_id in member_ids:
        add_coterie_member(conn, coterie["id"], char_id)

    now = _now()
    conn.execute("""
        UPDATE coterie_requests
        SET status='approved', reviewed_by=?, reviewed_at=?, coterie_id=?
        WHERE id=?
    """, (reviewer_id, now, coterie["id"], request_id))

    write_audit(conn, reviewer_id, "approve_coterie_request", "coterie_request", request_id,
                after={"coterie_id": coterie["id"]})

    # Notify the submitter + each member's player. Deduplicate so the
    # submitter doesn't double-ping when they're also in their own coterie.
    recipient_ids = {req["requested_by"]}
    for char_id in member_ids:
        char = get_character(conn, char_id)
        if char and char.get("discord_id"):
            recipient_ids.add(char["discord_id"])
    for discord_id in recipient_ids:
        enqueue_bot(conn, "coterie_request_approved", {
            "discord_id":   discord_id,
            "coterie_name": coterie["name"],
            "coterie_id":   coterie["id"],
        })
    return get_coterie_request(conn, request_id)


def reject_coterie_request(conn, request_id: int, reviewer_id: str, reason: str) -> dict:
    req = get_coterie_request(conn, request_id)
    if req is None:
        raise ValueError(f"Coterie request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is not pending")
    now = _now()
    conn.execute("""
        UPDATE coterie_requests
        SET status='rejected', reviewed_by=?, reviewed_at=?
        WHERE id=?
    """, (reviewer_id, now, request_id))
    write_audit(conn, reviewer_id, "reject_coterie_request", "coterie_request", request_id,
                after={"status": "rejected", "reason": reason})

    # Only the submitter is notified — the proposed members never opted in.
    enqueue_bot(conn, "coterie_request_rejected", {
        "discord_id":    req["requested_by"],
        "proposed_name": req["proposed_name"],
        "reason":        reason,
    })
    return get_coterie_request(conn, request_id)


def update_coterie(conn, coterie_id: int, **fields) -> dict:
    """Update whitelisted coterie fields (name, chasse, lien, portillon, status)."""
    allowed = {"name", "chasse", "lien", "portillon", "status", "discord_role_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_coterie(conn, coterie_id)
    updates["updated_at"] = _now()
    setters = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE coteries SET {setters} WHERE id=?",
        (*updates.values(), coterie_id),
    )
    return get_coterie(conn, coterie_id)


# ── Coterie spends ────────────────────────────────────────────────────────────

# XP cost for coterie domain upgrades: new_dots × 3, split evenly across members.
# Per-member cost always rounds up (ceiling division).
_DOMAIN_COST_PER_DOT = 3


def coterie_domain_cost(new_dots: int, member_count: int) -> tuple[int, int]:
    """Return (total_cost, per_member_cost) for a domain upgrade to new_dots."""
    if member_count <= 0:
        raise ValueError("Coterie must have at least one member")
    total = new_dots * _DOMAIN_COST_PER_DOT
    per   = -(-total // member_count)  # ceiling division
    return total, per


def get_coterie_spend(conn, spend_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM coterie_spends WHERE id=?", (spend_id,)).fetchone()
    return _parse(row, "contributions") if row else None


def list_coterie_spends(conn, coterie_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM coterie_spends WHERE coterie_id=? ORDER BY submitted_at DESC",
        (coterie_id,),
    ).fetchall()
    return [_parse(r, "contributions") for r in rows]


def list_pending_coterie_spends(conn) -> list[dict]:
    """All open proposals — both 'pending' (members still committing) and
    'funded' (ready for staff approval). Sorted so funded float to the
    top so staff sees what's actionable first."""
    rows = conn.execute(
        """SELECT * FROM coterie_spends
           WHERE status IN ('pending', 'funded')
           ORDER BY CASE status WHEN 'funded' THEN 0 ELSE 1 END,
                    submitted_at ASC"""
    ).fetchall()
    return [_parse(r, "contributions") for r in rows]


_COTERIE_SPEND_CATEGORIES = {"domain", "merit", "other"}


def create_coterie_spend(
    conn,
    coterie_id: int,
    trait_name: str,
    *,
    spend_category: str = "domain",
    current_dots: int = 0,
    new_dots: int = 0,
    per_member_cost: int | None = None,
    initiated_by: str | None = None,
    justification: str | None = None,
    contributions: dict | None = None,
) -> dict:
    """Open a coterie group-spend in 'pending' state. Members commit
    individually via commit_coterie_contribution() until everyone has
    paid in, at which point status flips to 'funded' and staff can
    approve.

    For domain upgrades (chasse / lien / portillon) the per-member cost
    auto-computes from coterie_domain_cost(); for merit/other categories
    the caller supplies per_member_cost directly. Old call-sites that
    pass only positional args still get the domain default."""
    if spend_category not in _COTERIE_SPEND_CATEGORIES:
        raise ValueError(f"Unknown spend category: {spend_category}")

    members = list_coterie_members(conn, coterie_id)
    if not members:
        raise ValueError("Coterie has no members")
    member_count = len(members)

    if spend_category == "domain":
        # Auto-compute from the V5 domain ladder.
        total_cost, per_member = coterie_domain_cost(new_dots, member_count)
    else:
        if per_member_cost is None or per_member_cost <= 0:
            raise ValueError("Non-domain coterie spends require a positive per_member_cost.")
        per_member = int(per_member_cost)
        total_cost = per_member * member_count
        # Use 0 placeholders — current_dots/new_dots are NOT NULL in the
        # original schema and only mean something for domain rows.
        current_dots = 0
        new_dots = 0

    cur = conn.execute("""
        INSERT INTO coterie_spends
            (coterie_id, trait_name, current_dots, new_dots,
             total_cost, per_member_cost, contributions,
             spend_category, initiated_by, justification, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (coterie_id, trait_name, current_dots, new_dots,
          total_cost, per_member,
          _j(contributions or {}),
          spend_category, initiated_by, justification, _now()))
    return get_coterie_spend(conn, cur.lastrowid)


def commit_coterie_contribution(
    conn,
    spend_id: int,
    character_id: int,
    by_staff: bool = False,
) -> dict:
    """Record a coterie member's XP commit on a pending spend. When the
    last member commits, the row flips to 'funded' and is ready for
    staff approval. Returns {spend, all_committed, members_remaining}.

    Raises ValueError on stale/missing spends, non-pending status, or
    if the character isn't a member of this coterie. by_staff is just
    for audit — both paths run the same logic."""
    spend = get_coterie_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Coterie spend {spend_id} not found")
    if spend["status"] != "pending":
        raise ValueError(f"Spend already {spend['status']} — can't commit more.")

    members = list_coterie_members(conn, spend["coterie_id"])
    if not any(m["character_id"] == character_id for m in members):
        raise ValueError("Character is not a member of this coterie.")

    char = get_character(conn, character_id)
    if char is None:
        raise ValueError("Character not found.")
    if (char.get("xp_available") or 0) < spend["per_member_cost"]:
        raise ValueError(
            f"{char['name']} only has {char.get('xp_available') or 0} XP available "
            f"(needs {spend['per_member_cost']})."
        )

    contributions = dict(spend.get("contributions") or {})
    contributions[str(character_id)] = spend["per_member_cost"]

    member_ids = {m["character_id"] for m in members}
    committed  = {int(k) for k in contributions.keys() if str(k).isdigit()}
    remaining  = sorted(member_ids - committed)
    new_status = "funded" if not remaining else "pending"

    conn.execute(
        "UPDATE coterie_spends SET contributions=?, status=? WHERE id=?",
        (_j(contributions), new_status, spend_id),
    )

    return {
        "spend": get_coterie_spend(conn, spend_id),
        "all_committed": not remaining,
        "members_remaining": remaining,
    }


def commit_all_coterie_contributions(
    conn,
    spend_id: int,
    reviewer_id: str,
) -> dict:
    """Staff-only shortcut: commit every uncommitted member in one go.
    Skips members who lack the XP rather than raising — staff sees the
    skip count and decides what to do."""
    spend = get_coterie_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Coterie spend {spend_id} not found")
    if spend["status"] != "pending":
        raise ValueError(f"Spend already {spend['status']} — can't commit more.")

    members = list_coterie_members(conn, spend["coterie_id"])
    contributions = dict(spend.get("contributions") or {})
    committed_ids = {int(k) for k in contributions.keys() if str(k).isdigit()}

    committed_now, skipped = 0, []
    for m in members:
        if m["character_id"] in committed_ids:
            continue
        char = get_character(conn, m["character_id"])
        if char is None:
            skipped.append({"character_id": m["character_id"], "reason": "missing"})
            continue
        if (char.get("xp_available") or 0) < spend["per_member_cost"]:
            skipped.append({"character_id": m["character_id"],
                            "reason": "low_xp",
                            "name": char["name"]})
            continue
        contributions[str(m["character_id"])] = spend["per_member_cost"]
        committed_now += 1

    member_ids = {m["character_id"] for m in members}
    new_committed = {int(k) for k in contributions.keys() if str(k).isdigit()}
    remaining = sorted(member_ids - new_committed)
    new_status = "funded" if not remaining else "pending"

    conn.execute(
        "UPDATE coterie_spends SET contributions=?, status=? WHERE id=?",
        (_j(contributions), new_status, spend_id),
    )
    write_audit(conn, reviewer_id, "commit_all_coterie_contributions",
                "coterie_spend", spend_id,
                after={"committed_now": committed_now, "skipped": len(skipped),
                       "new_status": new_status})
    return {
        "spend": get_coterie_spend(conn, spend_id),
        "committed_now": committed_now,
        "skipped": skipped,
        "all_committed": not remaining,
    }


def cancel_coterie_spend(conn, spend_id: int, character_id: int) -> dict:
    """The initiator can pull their own pending spend before staff acts
    on it. Refuses if the spend is already funded/approved/rejected, or
    if a different character is asking."""
    spend = get_coterie_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Coterie spend {spend_id} not found")
    if spend["status"] not in ("pending", "funded"):
        raise ValueError(f"Spend already {spend['status']} — can't cancel.")
    if spend.get("initiated_by") and str(spend["initiated_by"]) != str(character_id):
        # Stored as character_id for player submissions; staff submissions
        # have no initiator. We compare as strings to forgive type drift.
        raise ValueError("Only the initiating member can cancel this spend.")

    now = _now()
    conn.execute(
        "UPDATE coterie_spends SET status='rejected', reviewed_at=?, notes=? WHERE id=?",
        (now, "Cancelled by initiating member.", spend_id),
    )
    return get_coterie_spend(conn, spend_id)


def approve_coterie_spend(conn, spend_id: int, reviewer_id: str, notes: str | None = None) -> dict:
    """Finalize a Funded coterie spend: deduct each committing member's
    XP, write ledger entries, and apply the trait upgrade if it's a
    domain spend. Refuses Pending spends — every member must have
    committed first."""
    spend = get_coterie_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Coterie spend {spend_id} not found")
    if spend["status"] != "funded":
        raise ValueError(
            f"Coterie spend must be Funded before approval (status: {spend['status']}). "
            "Use the per-member commit flow first."
        )

    coterie = get_coterie(conn, spend["coterie_id"])
    if coterie is None:
        raise ValueError("Coterie not found")

    per_cost      = spend["per_member_cost"]
    category      = spend.get("spend_category") or "domain"
    contributions = spend.get("contributions") or {}
    # Use the recorded contribution map — that's who committed and
    # therefore who pays. Falls back to all members for legacy rows
    # written before the funding flow existed.
    if contributions:
        char_ids = [int(k) for k in contributions.keys() if str(k).isdigit()]
    else:
        char_ids = [m["character_id"] for m in list_coterie_members(conn, spend["coterie_id"])]

    # Verify XP balances before mutating anything — partial deduction
    # would leave the spend half-applied.
    chars = []
    for cid in char_ids:
        char = get_character(conn, cid)
        if char is None:
            raise ValueError(f"Character {cid} not found")
        if char["xp_available"] < per_cost:
            raise ValueError(
                f"{char['name']} only has {char['xp_available']} XP available "
                f"(need {per_cost})"
            )
        chars.append(char)

    # Ledger note depends on the category — domain shows dot progression,
    # merits/other just show the trait name.
    if category == "domain":
        ledger_note = (f"Coterie domain: {coterie['name']} — "
                       f"{spend['trait_name']} {spend['current_dots']}→{spend['new_dots']}")
    elif category == "merit":
        ledger_note = f"Coterie merit: {coterie['name']} — {spend['trait_name']}"
    else:
        ledger_note = f"Coterie spend: {coterie['name']} — {spend['trait_name']}"

    now = _now()
    for char in chars:
        conn.execute(
            "UPDATE characters SET xp_spent=xp_spent+?, updated_at=? WHERE id=?",
            (per_cost, now, char["id"]),
        )
        conn.execute("""
            INSERT INTO ledger_entries
                (character_id, entry_type, xp_delta, reference_id, reference_type, note, created_by, created_at)
            VALUES (?, 'spend', ?, ?, 'coterie_spend', ?, ?, ?)
        """, (
            char["id"], -per_cost, spend_id, ledger_note, reviewer_id, now,
        ))

    # Apply the upgrade based on the spend's recorded shape:
    #   - New-style spends carry contribution_type and target_kind so we
    #     can write a coterie_contributions row directly (which then drives
    #     the recompute of the cached chasse/lien/portillon columns).
    #   - Legacy domain group-buy spends still update the flat coterie
    #     column directly — but we ALSO write a 'staff_grant' contribution
    #     row so the audit trail stays unified going forward.
    new_style = bool(spend.get("contribution_type"))
    if new_style:
        ctype       = spend["contribution_type"]
        target_kind = "merit"  # default fallback; corrected below
        target_name: str | None = spend["trait_name"]
        # For C/L/P advances, trait_name was stored as the rating name
        # (e.g. "chasse"). Map back to the target_kind.
        if spend["trait_name"] in ("chasse", "lien", "portillon"):
            target_kind = spend["trait_name"]
            target_name = None
        elif category == "merit":
            target_kind = "merit"
        else:
            target_kind = "background"
        dots = (spend["new_dots"] - spend["current_dots"]) if spend["new_dots"] else 1
        funder_id = spend.get("funded_by_character_id") or (
            int(list(contributions.keys())[0]) if contributions else None
        )
        add_coterie_contribution(
            conn,
            coterie_id=spend["coterie_id"],
            contribution_type=ctype,
            target_kind=target_kind,
            target_name=target_name,
            dots=max(1, dots),
            character_id=funder_id,
            xp_paid=per_cost if ctype != "donated" else 0,
            period_id=spend.get("period_id"),
            spend_id=spend_id,
            note=f"approved by {reviewer_id}",
        )
        # Donations need the player's sheet entry flagged as shared too.
        if ctype == "donated" and funder_id and target_name:
            add_donation_share_to_sheet(
                conn, funder_id, spend["coterie_id"],
                target_kind, target_name,
            )
    elif category == "domain":
        # Legacy path — keep the flat column update for back-compat with
        # any spends submitted before migration 020 landed.
        update_coterie(conn, spend["coterie_id"], **{spend["trait_name"]: spend["new_dots"]})

    conn.execute("""
        UPDATE coterie_spends
        SET status='approved', reviewed_by=?, reviewed_at=?, notes=?
        WHERE id=?
    """, (reviewer_id, now, notes, spend_id))

    write_audit(conn, reviewer_id, "approve_coterie_spend", "coterie_spend", spend_id,
                after={"status": "approved", "trait": spend["trait_name"],
                       "category": category, "per_member_cost": per_cost,
                       "contributors": len(chars)})

    # Notify every contributing member — each one had XP deducted.
    for char in chars:
        if not char.get("discord_id"):
            continue
        enqueue_bot(conn, "coterie_spend_approved", {
            "discord_id":   char["discord_id"],
            "coterie_name": coterie["name"],
            "trait_name":   spend["trait_name"],
            "current_dots": spend["current_dots"],
            "new_dots":     spend["new_dots"],
            "per_member_cost": per_cost,
            "category":     category,
        })
    return get_coterie_spend(conn, spend_id)


def reject_coterie_spend(conn, spend_id: int, reviewer_id: str, reason: str) -> dict:
    spend = get_coterie_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Coterie spend {spend_id} not found")
    if spend["status"] not in ("pending", "funded"):
        raise ValueError(f"Coterie spend {spend_id} is already {spend['status']}")
    now = _now()
    conn.execute("""
        UPDATE coterie_spends
        SET status='rejected', reviewed_by=?, reviewed_at=?, notes=?
        WHERE id=?
    """, (reviewer_id, now, reason, spend_id))
    write_audit(conn, reviewer_id, "reject_coterie_spend", "coterie_spend", spend_id,
                after={"status": "rejected", "reason": reason})

    # Notify each coterie member — they were going to chip in XP, they
    # should know it didn't happen.
    coterie = get_coterie(conn, spend["coterie_id"])
    coterie_name = coterie["name"] if coterie else "your coterie"
    for m in list_coterie_members(conn, spend["coterie_id"]):
        char = get_character(conn, m["character_id"])
        if not (char and char.get("discord_id")):
            continue
        enqueue_bot(conn, "coterie_spend_rejected", {
            "discord_id":   char["discord_id"],
            "coterie_name": coterie_name,
            "trait_name":   spend["trait_name"],
            "reason":       reason,
        })
    return get_coterie_spend(conn, spend_id)


# ── Hunting Sites ─────────────────────────────────────────────────────────────

def _enrich_site(row: dict | None) -> dict | None:
    return _parse(row, "predator_dcs")


def get_hunting_site(conn, site_id: int) -> dict | None:
    return _enrich_site(
        conn.execute("SELECT * FROM hunting_sites WHERE id=?", (site_id,)).fetchone()
    )


def list_hunting_sites(conn, active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM hunting_sites"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY borough, name"
    return [_enrich_site(r) for r in conn.execute(sql).fetchall()]


def create_hunting_site(
    conn,
    name: str,
    borough: str,
    description: str = "",
    blood_quality: int = 1,
    predator_dcs: dict | None = None,
    coterie_id: int | None = None,
    is_contested: bool = False,
    active: bool = True,
    sect_control: str | None = None,
) -> dict:
    now = _now()
    cur = conn.execute("""
        INSERT INTO hunting_sites
            (name, borough, description, blood_quality, predator_dcs,
             coterie_id, is_contested, active, sect_control,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name, borough, description, max(1, min(5, blood_quality)),
        _j(predator_dcs or {}),
        coterie_id, int(is_contested),
        int(active), (sect_control or None),
        now, now,
    ))
    return get_hunting_site(conn, cur.lastrowid)


def update_hunting_site(conn, site_id: int, **fields) -> dict:
    ALLOWED = {"name", "borough", "description", "blood_quality", "predator_dcs",
               "coterie_id", "is_contested", "active", "sect_control"}
    safe = {k: v for k, v in fields.items() if k in ALLOWED}
    if not safe:
        return get_hunting_site(conn, site_id)
    if "predator_dcs" in safe and isinstance(safe["predator_dcs"], dict):
        safe["predator_dcs"] = _j(safe["predator_dcs"])
    if "is_contested" in safe:
        safe["is_contested"] = int(bool(safe["is_contested"]))
    safe["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in safe)
    conn.execute(f"UPDATE hunting_sites SET {sets} WHERE id=?", list(safe.values()) + [site_id])
    return get_hunting_site(conn, site_id)


def toggle_hunting_site(conn, site_id: int, actor_id: str | None = None) -> dict:
    before = get_hunting_site(conn, site_id) or {}
    conn.execute(
        "UPDATE hunting_sites SET active = NOT active, updated_at=? WHERE id=?",
        (_now(), site_id),
    )
    after = get_hunting_site(conn, site_id)
    if actor_id and after is not None:
        write_audit(conn, actor_id, "toggle_hunting_site", "hunting_site", site_id,
                    before={"active": bool(before.get("active"))},
                    after={"active": bool(after.get("active"))})
    return after


# ── Hunt logs ─────────────────────────────────────────────────────────────────

HUNT_OUTCOMES = ("clean", "success", "messy_critical", "bestial_failure")


def create_hunt_log(
    conn,
    site_id: int,
    character_id: int,
    outcome: str,
    note: str = "",
    source: str = "web",
) -> dict:
    if outcome not in HUNT_OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome}")
    if source not in ("web", "bot"):
        source = "web"
    cur = conn.execute("""
        INSERT INTO hunt_logs (site_id, character_id, outcome, note, source, hunted_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (site_id, character_id, outcome, (note or "")[:280], source, _now()))
    return conn.execute(
        "SELECT * FROM hunt_logs WHERE id=?", (cur.lastrowid,)
    ).fetchone()


def list_hunts_for_site(conn, site_id: int, limit: int = 20) -> list[dict]:
    """Hunts at a site, newest first, with character name + clan joined."""
    return conn.execute("""
        SELECT hl.*, c.name AS character_name, c.clan AS character_clan
        FROM hunt_logs hl
        JOIN characters c ON c.id = hl.character_id
        WHERE hl.site_id = ?
        ORDER BY hl.hunted_at DESC
        LIMIT ?
    """, (site_id, limit)).fetchall()


def list_hunts_for_character(conn, character_id: int, limit: int = 20) -> list[dict]:
    """A character's hunting trail, with site name + borough joined."""
    return conn.execute("""
        SELECT hl.*, s.name AS site_name, s.borough AS site_borough
        FROM hunt_logs hl
        JOIN hunting_sites s ON s.id = hl.site_id
        WHERE hl.character_id = ?
        ORDER BY hl.hunted_at DESC
        LIMIT ?
    """, (character_id, limit)).fetchall()


# ── Chronicle Map ─────────────────────────────────────────────────────────────

def _enrich_map_feature(row: dict | None) -> dict | None:
    """Decode geometry_json into a dict so callers get a usable
    GeoJSON Geometry object back, not a TEXT blob."""
    if row is None:
        return None
    out = dict(row)
    if isinstance(out.get("geometry_json"), str):
        try:
            out["geometry"] = json.loads(out["geometry_json"])
        except (ValueError, TypeError):
            out["geometry"] = None
    return out


_MAP_VISIBILITIES = {"public", "staff"}


def list_map_layers(conn, include_staff_only: bool = False, active_only: bool = True) -> list[dict]:
    """Layers sorted by sort_order ascending. include_staff_only=True
    returns every layer (use that on the staff editor); the default
    filters down to public layers for the player view."""
    where = []
    if active_only:
        where.append("active=1")
    if not include_staff_only:
        where.append("visibility='public'")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(
        f"SELECT * FROM map_layers {clause} ORDER BY sort_order ASC, id ASC"
    ).fetchall()


def get_map_layer(conn, layer_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM map_layers WHERE id=?", (layer_id,)
    ).fetchone()


def create_map_layer(
    conn,
    name: str,
    *,
    description: str | None = None,
    color: str = "#8B1A1A",
    visibility: str = "public",
    sort_order: int = 0,
    created_by: str | None = None,
) -> dict:
    if visibility not in _MAP_VISIBILITIES:
        raise ValueError(f"Unknown layer visibility: {visibility}")
    now = _now()
    cur = conn.execute("""
        INSERT INTO map_layers
            (name, description, color, visibility, sort_order, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, description, color, visibility, sort_order, created_by, now, now))
    row = get_map_layer(conn, cur.lastrowid)
    if created_by:
        write_audit(conn, created_by, "create_map_layer", "map_layer", row["id"],
                    after={"name": name, "visibility": visibility, "color": color})
    return row


def update_map_layer(conn, layer_id: int, actor_id: str | None = None, **fields) -> dict:
    ALLOWED = {"name", "description", "color", "visibility", "sort_order", "active"}
    safe = {k: v for k, v in fields.items() if k in ALLOWED}
    if not safe:
        return get_map_layer(conn, layer_id)
    if "visibility" in safe and safe["visibility"] not in _MAP_VISIBILITIES:
        raise ValueError(f"Unknown layer visibility: {safe['visibility']}")
    before = get_map_layer(conn, layer_id) or {}
    safe["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in safe.keys())
    params = list(safe.values()) + [layer_id]
    conn.execute(f"UPDATE map_layers SET {sets} WHERE id=?", params)
    after = get_map_layer(conn, layer_id)
    if actor_id and after is not None:
        diff = {k: after.get(k) for k in safe.keys()
                if k != "updated_at" and before.get(k) != after.get(k)}
        if diff:
            write_audit(conn, actor_id, "update_map_layer", "map_layer", layer_id,
                        after=diff)
    return after


def delete_map_layer(conn, layer_id: int, actor_id: str | None = None) -> None:
    """Cascade-deletes via the foreign key constraint on map_features."""
    row = get_map_layer(conn, layer_id)
    conn.execute("DELETE FROM map_features WHERE layer_id=?", (layer_id,))
    conn.execute("DELETE FROM map_layers WHERE id=?", (layer_id,))
    if actor_id and row is not None:
        write_audit(conn, actor_id, "delete_map_layer", "map_layer", layer_id,
                    before={"name": row.get("name"), "visibility": row.get("visibility")})


def list_map_features(
    conn,
    layer_id: int | None = None,
    include_hidden: bool = False,
) -> list[dict]:
    where = []
    params: list = []
    if layer_id is not None:
        where.append("layer_id=?")
        params.append(layer_id)
    if not include_hidden:
        where.append("is_hidden=0")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT * FROM map_features {clause} ORDER BY id ASC", params
    ).fetchall()
    return [_enrich_map_feature(r) for r in rows]


def get_map_feature(conn, feature_id: int) -> dict | None:
    return _enrich_map_feature(
        conn.execute("SELECT * FROM map_features WHERE id=?", (feature_id,)).fetchone()
    )


_FEATURE_TYPES = {"point", "polygon", "line"}


def create_map_feature(
    conn,
    layer_id: int,
    *,
    label: str = "",
    description: str | None = None,
    tag: str | None = None,
    feature_type: str,
    geometry: dict,
    coterie_id: int | None = None,
    site_id: int | None = None,
    is_hidden: bool = False,
    actor_id: str | None = None,
) -> dict:
    if feature_type not in _FEATURE_TYPES:
        raise ValueError(f"Unknown feature type: {feature_type}")
    if not isinstance(geometry, dict) or "type" not in geometry:
        raise ValueError("geometry must be a GeoJSON Geometry object")
    cur = conn.execute("""
        INSERT INTO map_features
            (layer_id, label, description, tag, feature_type, geometry_json,
             coterie_id, site_id, is_hidden, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (layer_id, label, description, tag, feature_type, _j(geometry),
          coterie_id, site_id, int(bool(is_hidden)), _now()))
    row = get_map_feature(conn, cur.lastrowid)
    if actor_id:
        write_audit(conn, actor_id, "create_map_feature", "map_feature", row["id"],
                    after={"layer_id": layer_id, "label": label,
                           "feature_type": feature_type, "tag": tag})
    return row


def update_map_feature(conn, feature_id: int, actor_id: str | None = None, **fields) -> dict:
    ALLOWED = {"label", "description", "tag", "coterie_id", "site_id", "is_hidden"}
    safe = {k: v for k, v in fields.items() if k in ALLOWED}
    if "is_hidden" in safe:
        safe["is_hidden"] = int(bool(safe["is_hidden"]))
    if not safe:
        return get_map_feature(conn, feature_id)
    before = get_map_feature(conn, feature_id) or {}
    sets = ", ".join(f"{k}=?" for k in safe.keys())
    params = list(safe.values()) + [feature_id]
    conn.execute(f"UPDATE map_features SET {sets} WHERE id=?", params)
    after = get_map_feature(conn, feature_id)
    if actor_id and after is not None:
        diff = {k: after.get(k) for k in safe.keys()
                if before.get(k) != after.get(k)}
        if diff:
            write_audit(conn, actor_id, "update_map_feature", "map_feature",
                        feature_id, after=diff)
    return after


def delete_map_feature(conn, feature_id: int, actor_id: str | None = None) -> None:
    row = get_map_feature(conn, feature_id)
    conn.execute("DELETE FROM map_features WHERE id=?", (feature_id,))
    if actor_id and row is not None:
        write_audit(conn, actor_id, "delete_map_feature", "map_feature", feature_id,
                    before={"layer_id": row.get("layer_id"),
                            "label": row.get("label")})


# ── GeoJSON + KML import ─────────────────────────────────────────────────────

def _geojson_type_to_feature_type(geom_type: str) -> str | None:
    """Map a GeoJSON geometry type to our 3-value feature_type."""
    geom_type = (geom_type or "").lower()
    if geom_type in ("point", "multipoint"):
        return "point"
    if geom_type in ("polygon", "multipolygon"):
        return "polygon"
    if geom_type in ("linestring", "multilinestring"):
        return "line"
    return None


def import_geojson(
    conn,
    layer_id: int,
    geojson: dict,
    *,
    label_field: str | None = None,
    tag_field: str | None = None,
    description_field: str | None = None,
) -> dict:
    """Bulk-insert features from a GeoJSON FeatureCollection (or a
    bare Feature). Returns {inserted, skipped, errors}. Properties
    named by label_field/tag_field/description_field are pulled into
    the matching columns; everything else stays in the geometry."""
    inserted, skipped, errors = 0, 0, []

    if not isinstance(geojson, dict):
        return {"inserted": 0, "skipped": 0, "errors": ["payload is not a JSON object"]}

    # Accept either a FeatureCollection or a single Feature.
    if geojson.get("type") == "FeatureCollection":
        features = geojson.get("features") or []
    elif geojson.get("type") == "Feature":
        features = [geojson]
    else:
        return {"inserted": 0, "skipped": 0,
                "errors": [f"Top-level type must be FeatureCollection or Feature (got {geojson.get('type')!r})"]}

    for idx, feat in enumerate(features):
        try:
            geom = (feat or {}).get("geometry") or {}
            props = (feat or {}).get("properties") or {}
            ftype = _geojson_type_to_feature_type(geom.get("type", ""))
            if not ftype:
                skipped += 1
                continue
            label = ""
            if label_field and isinstance(props.get(label_field), str):
                label = props[label_field][:120]
            elif "name" in props and isinstance(props["name"], str):
                label = props["name"][:120]
            elif "title" in props and isinstance(props["title"], str):
                label = props["title"][:120]
            tag = None
            if tag_field and props.get(tag_field) is not None:
                tag = str(props[tag_field])[:60]
            desc = None
            if description_field and isinstance(props.get(description_field), str):
                desc = props[description_field]
            elif isinstance(props.get("description"), str):
                desc = props["description"]
            create_map_feature(
                conn, layer_id=layer_id,
                label=label, description=desc, tag=tag,
                feature_type=ftype, geometry=geom,
            )
            inserted += 1
        except Exception as e:  # noqa: BLE001 — surface per-feature failures
            errors.append(f"feature #{idx}: {e}")
            skipped += 1

    return {"inserted": inserted, "skipped": skipped, "errors": errors}


def import_kml(conn, layer_id: int, kml_text: str) -> dict:
    """Parse a Google Maps "My Maps" KML export and import its
    placemarks as map features. Handles Point / Polygon / LineString.
    Multi-geometry placemarks are flattened into individual features.

    Uses the stdlib XML parser; no extra dependencies."""
    from xml.etree import ElementTree as ET
    inserted, skipped, errors = 0, 0, []

    # KML uses a default namespace — strip it so XPath queries don't need
    # to prefix every tag. Quick + dirty but fine for our import shape.
    try:
        root = ET.fromstring(kml_text)
    except ET.ParseError as e:
        return {"inserted": 0, "skipped": 0, "errors": [f"KML parse error: {e}"]}

    def _strip_ns(tag: str) -> str:
        return tag.split("}", 1)[1] if tag.startswith("{") else tag

    def _walk(el):
        if _strip_ns(el.tag) == "Placemark":
            yield el
        for child in el:
            yield from _walk(child)

    def _parse_coords(text: str) -> list[list[float]]:
        """KML coords are 'lon,lat[,alt] lon,lat[,alt] …' — return
        a list of [lon, lat] pairs (GeoJSON order)."""
        pairs = []
        for token in (text or "").split():
            parts = token.split(",")
            if len(parts) >= 2:
                try:
                    pairs.append([float(parts[0]), float(parts[1])])
                except ValueError:
                    continue
        return pairs

    def _child_text(el, name: str) -> str:
        for c in el:
            if _strip_ns(c.tag) == name and c.text:
                return c.text.strip()
        return ""

    def _find_geometries(el):
        """Yield (kml_type, coords_element) tuples for every geometry
        nested under a Placemark. MultiGeometry containers are expanded."""
        kind = _strip_ns(el.tag)
        if kind in ("Point", "LineString", "Polygon"):
            yield (kind, el)
        else:
            for child in el:
                yield from _find_geometries(child)

    for pm in _walk(root):
        try:
            label = _child_text(pm, "name")[:120]
            desc  = _child_text(pm, "description") or None
            for kind, geom_el in _find_geometries(pm):
                if kind == "Point":
                    coords_text = ""
                    for c in geom_el:
                        if _strip_ns(c.tag) == "coordinates":
                            coords_text = c.text or ""
                    pairs = _parse_coords(coords_text)
                    if not pairs:
                        skipped += 1
                        continue
                    geometry = {"type": "Point", "coordinates": pairs[0]}
                    ftype = "point"
                elif kind == "LineString":
                    coords_text = ""
                    for c in geom_el:
                        if _strip_ns(c.tag) == "coordinates":
                            coords_text = c.text or ""
                    pairs = _parse_coords(coords_text)
                    if len(pairs) < 2:
                        skipped += 1
                        continue
                    geometry = {"type": "LineString", "coordinates": pairs}
                    ftype = "line"
                else:  # Polygon
                    # Pull the outer LinearRing only — holes (inner rings)
                    # exist in KML but are rare for chronicle maps.
                    outer_coords = ""
                    for c in geom_el.iter():
                        if _strip_ns(c.tag) == "outerBoundaryIs":
                            for cc in c.iter():
                                if _strip_ns(cc.tag) == "coordinates":
                                    outer_coords = cc.text or ""
                                    break
                            break
                    pairs = _parse_coords(outer_coords)
                    if len(pairs) < 3:
                        skipped += 1
                        continue
                    # GeoJSON Polygon coordinates: array of LinearRings,
                    # first/last point must match.
                    if pairs[0] != pairs[-1]:
                        pairs.append(pairs[0])
                    geometry = {"type": "Polygon", "coordinates": [pairs]}
                    ftype = "polygon"

                create_map_feature(
                    conn, layer_id=layer_id,
                    label=label, description=desc,
                    feature_type=ftype, geometry=geometry,
                )
                inserted += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"placemark {label!r}: {e}")
            skipped += 1

    return {"inserted": inserted, "skipped": skipped, "errors": errors}


# ── Bot Outbox ────────────────────────────────────────────────────────────────

def enqueue_bot(conn, command: str, payload: dict) -> dict:
    cur = conn.execute("""
        INSERT INTO bot_outbox (command, payload, created_at)
        VALUES (?, ?, ?)
    """, (command, _j(payload), _now()))
    row = conn.execute("SELECT * FROM bot_outbox WHERE id=?", (cur.lastrowid,)).fetchone()
    return _parse(row, "payload")


def drain_outbox(conn, limit: int = 10) -> list[dict]:
    """
    Fetch pending bot_outbox rows and mark them 'processing'.
    The bot calls this, processes each command, then calls ack_outbox.
    """
    rows = conn.execute("""
        SELECT * FROM bot_outbox
        WHERE status='pending'
        ORDER BY created_at ASC
        LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"""
        UPDATE bot_outbox
        SET status='processing', attempts=attempts+1
        WHERE id IN ({placeholders})
    """, ids)
    return [_parse(r, "payload") for r in rows]


def ack_outbox(conn, outbox_id: int, success: bool = True, error: str | None = None) -> None:
    conn.execute("""
        UPDATE bot_outbox SET status=?, error=?, processed_at=? WHERE id=?
    """, ("done" if success else "failed", error, _now(), outbox_id))
