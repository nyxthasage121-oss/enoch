"""db.py — Database connection, migrations, and query helpers.

Local SQLite files use the stdlib sqlite3 driver; only a Turso URL
(libsql:// or https://) uses libsql-experimental. We keep local files on
stdlib sqlite3 on purpose: libsql's Connection is a compiled type that
rejects a custom row_factory on some Python builds, which crash-loops boot.
"""
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlite3  # stdlib — drives local SQLite files (reliable row_factory)

try:
    import libsql_experimental as libsql
except ModuleNotFoundError:
    libsql = sqlite3  # type: ignore[assignment]  # libsql only used for Turso URLs

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

def _connect():
    url   = settings.DATABASE_URL
    token = settings.TURSO_AUTH_TOKEN
    if url.startswith("libsql") or url.startswith("https://"):
        # Turso (remote) — the only path that needs libsql-experimental.
        conn = libsql.connect(database=url, auth_token=token)
    else:
        # Local SQLite file → stdlib sqlite3. libsql's Connection rejects
        # `conn.row_factory = ...` on some Python builds (a compiled type with
        # no __dict__), which crash-loops the app on boot. stdlib sqlite3
        # supports the custom row_factory and is correct for local files.
        conn = sqlite3.connect(url)
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
    """Apply any pending numbered *.sql files from the migrations/ directory.

    Each file is applied inside an explicit transaction *together with* its
    _migrations bookkeeping row, so a file either applies in full and is
    recorded, or rolls back entirely. The explicit ``BEGIN`` matters: under
    SQLite's default isolation, bare DDL (CREATE/ALTER) auto-commits one
    statement at a time, so a failure partway through a file could leave the
    schema half-migrated yet unrecorded — and the file would re-run on the
    next boot, crashing on the statements that already landed. With the
    transaction, a failed migration rolls back cleanly and halts startup
    loudly (naming the file) so it gets fixed rather than silently looping.
    """
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
        sql_clean = re.sub(r"--[^\n]*", "", sql)
        statements = [s.strip() for s in sql_clean.split(";") if s.strip()]
        try:
            with get_db() as conn:
                # Explicit transaction so DDL joins the rollback scope; get_db()
                # commits on a clean exit and rolls back on any exception.
                conn.execute("BEGIN")
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO _migrations (filename) VALUES (?)", (path.name,)
                )
        except Exception:
            log.exception(
                "Migration %s failed and was rolled back; halting startup",
                path.name,
            )
            raise
        log.info("Applied: %s", path.name)


# ── Player Profiles ───────────────────────────────────────────────────────────

def get_player(conn, discord_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM player_profiles WHERE discord_id=?", (discord_id,)
    ).fetchone()


# ── Staff role + permission matrix ───────────────────────────────────────────

# Canonical role list — matches the Discord server's staff roles, highest
# authority first (order drives the admin dropdown).
STAFF_ROLES = ("admin", "moderator", "storyteller", "helper")

# Display labels for the staff roles — the SINGLE source of truth for the web
# side (main.py's _ctx + the Admin role picker import this; the bot mirrors it
# in bot/cogs/staff.py since slash-command choices must be static at import).
STAFF_ROLE_LABELS: dict[str, str] = {
    "admin":       "Admin",
    "moderator":   "Moderator",
    "storyteller": "Storyteller",
    "helper":      "Helper",
}

# Full game-staff permission set: everything a Storyteller needs to run XP and
# the chronicle, short of chronicle-wide settings + role management.
_STORYTELLER_PERMS: set[str] = {
    "approve_claim", "approve_spend", "approve_character", "reject_character",
    "edit_character", "delete_character", "adjust_xp",
    "manage_period", "manage_coterie", "manage_criteria", "manage_site",
    "manage_map", "manage_project",
}

# Permission matrix. Roles are checked against permission keys. Anything
# not listed defaults to denied. Keep keys short + verb-noun-y so route
# wiring reads naturally (require_permission("manage_settings"), etc.).
STAFF_PERMISSIONS: dict[str, set[str]] = {
    # Admin — full control; the only role that can manage settings + assign roles.
    "admin": _STORYTELLER_PERMS | {"manage_settings", "manage_roles"},
    # Moderator — full XP + chronicle management, no settings/roles. Same Enoch
    # powers as Storyteller; the difference is organizational (also a server mod).
    "moderator": set(_STORYTELLER_PERMS),
    # Storyteller — "XP in general": award, approve spends, manual adjust, plus
    # character/period/coterie management. No settings/roles.
    "storyteller": set(_STORYTELLER_PERMS),
    # Helper — spends only: can approve trait-spend requests, nothing else.
    "helper": {"approve_spend"},
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
    target: str = "total",
) -> dict:
    """Manual XP adjustment by staff. `delta` is the signed impact on the
    character's *available* XP (>0 raises available, <0 lowers it).

    target="total" moves earned XP (xp_total) — grant / remove. This is the
        cap-relevant figure shown as "X / cap".
    target="spent" moves consumed XP (xp_spent) — refund / add-spend. The
        earned total is left untouched; available still shifts by `delta`
        because xp_spent moves by -delta.
    Both floor the adjusted column at 0. The ledger always records `delta`
    (the available impact) so the history reads consistently."""
    char = get_character(conn, character_id)
    if char is None:
        raise ValueError(f"Character {character_id} not found")
    if not note.strip():
        raise ValueError("A note is required for manual adjustments.")
    now = _now()
    if target == "spent":
        new_spent = max(0, char["xp_spent"] - delta)
        conn.execute(
            "UPDATE characters SET xp_spent=?, updated_at=? WHERE id=?",
            (new_spent, now, character_id),
        )
        audit_before = {"xp_spent": char["xp_spent"]}
        audit_after  = {"xp_spent": new_spent, "delta": delta, "note": note}
    else:
        new_total = max(0, char["xp_total"] + delta)
        conn.execute(
            "UPDATE characters SET xp_total=?, updated_at=? WHERE id=?",
            (new_total, now, character_id),
        )
        audit_before = {"xp_total": char["xp_total"]}
        audit_after  = {"xp_total": new_total, "delta": delta, "note": note}
    conn.execute("""
        INSERT INTO ledger_entries
            (character_id, entry_type, xp_delta, reference_type, note, created_by, created_at)
        VALUES (?, 'adjustment', ?, 'manual', ?, ?, ?)
    """, (character_id, delta, note, staff_id, now))
    write_audit(conn, staff_id, "adjust_xp", "character", character_id,
                before=audit_before, after=audit_after)
    return get_character(conn, character_id)


def resolve_bulk_xp(conn, text: str) -> tuple[list[dict], list[str]]:
    """Parse a bulk-XP textarea — one `<amount> <character name>` per line — and
    resolve each line to an *active* character by exact (case-insensitive) name.

    Returns ``(awards, errors)``. Each award is
    ``{character_id, name, clan, player, amount}``; ``errors`` is a list of
    human-readable strings. A line errors if it can't be parsed, the amount
    isn't a positive whole number, the name matches no active character, the
    name is ambiguous (>1 active character), or the same character is listed
    twice. Callers should refuse to commit when ``errors`` is non-empty —
    all-or-nothing, so one typo never produces a partial award."""
    by_name: dict[str, list[dict]] = {}
    for c in list_characters(conn, status="active"):
        by_name.setdefault(c["name"].strip().lower(), []).append(c)

    awards: list[dict] = []
    errors: list[str] = []
    seen: set[int] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            errors.append(f"Couldn't read “{line}” — use `<amount> <character name>`.")
            continue
        amount_str, name = parts[0], parts[1].strip()
        try:
            amount = int(amount_str)
        except ValueError:
            errors.append(f"“{line}” — “{amount_str}” isn't a whole number.")
            continue
        if amount <= 0:
            errors.append(f"“{name}” — amount must be a positive whole number.")
            continue
        matches = by_name.get(name.lower(), [])
        if not matches:
            errors.append(f"No active character named “{name}”.")
            continue
        if len(matches) > 1:
            errors.append(
                f"“{name}” is ambiguous — {len(matches)} active characters share that name.")
            continue
        c = matches[0]
        if c["id"] in seen:
            errors.append(f"“{name}” is listed more than once.")
            continue
        seen.add(c["id"])
        awards.append({
            "character_id": c["id"], "name": c["name"], "clan": c.get("clan"),
            "player": c.get("player_username"), "amount": amount,
        })
    return awards, errors


def apply_bulk_xp(conn, awards: list[dict], note: str, staff_id: str) -> int:
    """Apply a resolved, error-free list of awards. Each grants `amount` XP to
    the character's earned total (target='total'), via the same `adjust_xp_manual`
    path as a single manual grant — so each lands a ledger + audit row. Runs
    inside the caller's get_db() block, so the whole batch is one transaction.
    Returns the number of awards applied."""
    for a in awards:
        adjust_xp_manual(conn, a["character_id"], int(a["amount"]),
                         note, staff_id, target="total")
    return len(awards)


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
    so they don't trip the warning. Uses the chronicle-wide cap amount and
    returns nothing when the cap is disabled (nobody is "near" a cap)."""
    settings = get_settings(conn)
    if not bool((settings or {}).get("xp_cap_enabled", 1)):
        return []
    cap_amount = int((settings or {}).get("xp_cap_amount", 350) or 350)
    return conn.execute(
        """
        SELECT c.*, pp.username AS player_username,
               (? - (c.xp_total - c.creation_xp)) AS xp_to_cap
        FROM characters c
        LEFT JOIN player_profiles pp ON pp.discord_id = c.discord_id
        WHERE c.is_approved = 1
          AND c.status = 'active'
          AND (? - (c.xp_total - c.creation_xp)) BETWEEN 0 AND ?
        ORDER BY xp_to_cap ASC, c.name
        """,
        (cap_amount, cap_amount, threshold_xp),
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


def count_active_player_characters(conn, discord_id: str) -> int:
    """Count a player's characters that occupy a 'slot' for the per-player
    cap: active + pending (awaiting approval). Drafts, retired, and Final
    Death characters don't count."""
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM characters
        WHERE discord_id=? AND COALESCE(is_draft, 0)=0
          AND status NOT IN ('retired', 'dead')
    """, (str(discord_id),)).fetchone()
    return int(row["n"]) if row else 0


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
    # Character Creation (CC) XP accounting — posted ONCE when the character
    # first goes active (guarded against re-approval). The per-trait breakdown
    # stays in sheet_json for staff; here we (a) note the lump spend, and (b)
    # carry any LEFTOVER pool XP into the running total. Leftover XP is tracked
    # in creation_xp so it stays exempt from the chronicle XP cap.
    _sheet = char.get("sheet_json") if isinstance(char.get("sheet_json"), dict) else {}
    try:
        _cc_spent = int(_sheet.get("xp_spent") or 0)
    except (TypeError, ValueError):
        _cc_spent = 0
    _raw_pool = _sheet.get("starting_xp_pool")
    if _raw_pool is None:
        # No recorded pool (older / staff-seeded character) — fall back to the
        # tier's finishing-touches XP, but ONLY for Kindred. Mortals, ghouls, and
        # revenants have no creation-XP pool, yet their character_tier column
        # defaults to "neonate", so a tier lookup would wrongly hand them 15 XP.
        if char.get("character_type") == "kindred":
            _pool = int((tier_budget(get_settings(conn), char.get("character_tier")) or {}).get("xp") or 0)
        else:
            _pool = 0
    else:
        # A recorded pool is authoritative — a genuine 0 (mortals, or a tier with
        # no finishing XP) must carry nothing over, never the tier default.
        try:
            _pool = int(_raw_pool or 0)
        except (TypeError, ValueError):
            _pool = 0
    _leftover = max(0, _pool - _cc_spent)
    # Guard on EITHER kind of creation-time entry so a character that spent no
    # CC-XP (only carried leftover) still can't double-post on re-approval.
    _exists = conn.execute(
        "SELECT 1 FROM ledger_entries WHERE character_id=? "
        "AND entry_type IN ('creation','carryover') LIMIT 1",
        (character_id,),
    ).fetchone()
    if not _exists:
        if _cc_spent > 0:
            append_ledger_entry(conn, character_id, "creation", -_cc_spent, reviewer_id,
                                note="Character Creation XP spent")
        if _leftover > 0:
            conn.execute(
                "UPDATE characters SET xp_total = xp_total + ?, creation_xp = ? WHERE id=?",
                (_leftover, _leftover, character_id),
            )
            # Distinct entry_type so the CC spend stays a single clean "creation"
            # entry; this leftover is carried into the total but stays cap-exempt.
            append_ledger_entry(conn, character_id, "carryover", _leftover, reviewer_id,
                                note="Leftover creation XP — does not count toward the cap")
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
    conn.execute("DELETE FROM companions WHERE parent_character_id=?", (character_id,))
    conn.execute("DELETE FROM character_familiars WHERE character_id=?", (character_id,))
    conn.execute("DELETE FROM characters WHERE id=?", (character_id,))


# ── Companions (Retainers & Mawlas) ─────────────────────────────────────────

def _companion_view(row: dict | None) -> dict | None:
    """Parse a companions row's JSON + coerce the ghoul flag to bool."""
    row = _parse(row, "sheet_json")
    if row is None:
        return None
    if not isinstance(row.get("sheet_json"), dict):
        row["sheet_json"] = {}
    row["is_ghoul"] = bool(row.get("is_ghoul"))
    return row


def list_companions(conn, character_id: int, kind: str | None = None) -> list[dict]:
    """All companions for a character, optionally filtered to 'retainer'/'mawla'."""
    if kind:
        rows = conn.execute(
            "SELECT * FROM companions WHERE parent_character_id=? AND kind=? "
            "ORDER BY kind, name", (character_id, kind)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM companions WHERE parent_character_id=? "
            "ORDER BY kind, name", (character_id,)).fetchall()
    return [_companion_view(r) for r in rows]


def get_companion(conn, companion_id: int) -> dict | None:
    return _companion_view(
        conn.execute("SELECT * FROM companions WHERE id=?", (companion_id,)).fetchone())


def get_companion_for_player(conn, companion_id: int, discord_id: str) -> dict | None:
    """A companion only if its parent character belongs to the given player —
    the ownership gate for player-side companion routes."""
    return _companion_view(conn.execute(
        "SELECT c.* FROM companions c "
        "JOIN characters ch ON ch.id = c.parent_character_id "
        "WHERE c.id=? AND ch.discord_id=?", (companion_id, discord_id)).fetchone())


def create_companion(conn, *, parent_character_id: int, kind: str, name: str,
                     dots: int = 1, template: str | None = None,
                     is_ghoul: bool = False, clan: str | None = None,
                     concept: str | None = None, description: str | None = None,
                     sheet_json: dict | None = None, bg_key: str | None = None) -> dict:
    """Insert a companion (rides the parent's approval — no separate review)."""
    now = _now()
    cur = conn.execute("""
        INSERT INTO companions
            (parent_character_id, kind, name, dots, template, is_ghoul, clan,
             concept, description, sheet_json, bg_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (parent_character_id, kind, name, int(dots or 0), template,
          int(bool(is_ghoul)), clan, concept, description,
          _j(sheet_json or {}), bg_key, now, now))
    return get_companion(conn, cur.lastrowid)


def update_companion(conn, companion_id: int, **fields) -> dict | None:
    """Patch a companion. Only whitelisted columns are writable."""
    allowed = {"kind", "name", "dots", "template", "is_ghoul", "clan",
               "concept", "description", "sheet_json", "bg_key"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "sheet_json":
            v = _j(v or {})
        elif k == "is_ghoul":
            v = int(bool(v))
        elif k == "dots":
            v = int(v or 0)
        sets.append(f"{k}=?")
        vals.append(v)
    if sets:
        sets.append("updated_at=?")
        vals.extend([_now(), companion_id])
        conn.execute(f"UPDATE companions SET {', '.join(sets)} WHERE id=?", vals)
    return get_companion(conn, companion_id)


def delete_companion(conn, companion_id: int) -> None:
    conn.execute("DELETE FROM companions WHERE id=?", (companion_id,))


# ── Familiars (Animalism • Bond Famulus) ─────────────────────────────────────

def _familiar_view(row: dict | None) -> dict | None:
    """Parse a familiars row's exceptional-pools JSON + coerce the flag."""
    row = _parse(row, "exceptional")
    if row is None:
        return None
    if not isinstance(row.get("exceptional"), dict):
        row["exceptional"] = {}
    if "is_standard" in row:
        row["is_standard"] = bool(row.get("is_standard"))
    return row


def list_familiars(conn) -> list[dict]:
    """The global animal catalog — V5 standards first, then staff customs."""
    rows = conn.execute(
        "SELECT * FROM familiars ORDER BY is_standard DESC, sort_order, name").fetchall()
    return [_familiar_view(r) for r in rows]


def get_familiar(conn, familiar_id: int) -> dict | None:
    return _familiar_view(
        conn.execute("SELECT * FROM familiars WHERE id=?", (familiar_id,)).fetchone())


def create_familiar(conn, *, name: str, description: str | None = None,
                    physical: int = 1, social: int = 1, mental: int = 1,
                    health: int = 1, willpower: int = 1, exceptional: dict | None = None,
                    special: str | None = None, created_by: str = "") -> dict:
    """Add a custom animal to the global catalog (is_standard=0)."""
    nxt = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM familiars").fetchone()
    cur = conn.execute("""
        INSERT INTO familiars
            (name, description, physical, social, mental, health, willpower,
             exceptional, special, is_standard, sort_order, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
    """, (name, description, int(physical), int(social), int(mental), int(health),
          int(willpower), _j(exceptional or {}), special,
          (nxt["n"] if nxt else 1), created_by, _now()))
    return get_familiar(conn, cur.lastrowid)


def update_familiar(conn, familiar_id: int, **fields) -> dict | None:
    """Patch a custom catalog animal. Whitelisted columns only."""
    allowed = {"name", "description", "physical", "social", "mental", "health",
               "willpower", "exceptional", "special"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "exceptional":
            v = _j(v or {})
        elif k in ("physical", "social", "mental", "health", "willpower"):
            v = int(v or 0)
        sets.append(f"{k}=?")
        vals.append(v)
    if sets:
        vals.append(familiar_id)
        conn.execute(f"UPDATE familiars SET {', '.join(sets)} WHERE id=?", vals)
    return get_familiar(conn, familiar_id)


def delete_familiar(conn, familiar_id: int) -> bool:
    """Delete a CUSTOM catalog animal — V5 standards are protected. Returns True
    if a row was removed."""
    row = conn.execute("SELECT is_standard FROM familiars WHERE id=?",
                       (familiar_id,)).fetchone()
    if not row or row.get("is_standard"):
        return False
    conn.execute("DELETE FROM familiars WHERE id=?", (familiar_id,))
    return True


def _character_familiar_view(row: dict | None) -> dict | None:
    row = _parse(row, "exceptional")
    if row is None:
        return None
    if not isinstance(row.get("exceptional"), dict):
        row["exceptional"] = {}
    # Animal-type label: the live catalog name, else the denormalized snapshot.
    row["animal"] = row.get("catalog_name") or row.get("animal_name") or "Unknown animal"
    return row


_CHAR_FAMILIAR_SELECT = """
    SELECT cf.id, cf.character_id, cf.familiar_id, cf.name, cf.notes,
           cf.animal_name, cf.created_at,
           f.name AS catalog_name, f.description, f.physical, f.social,
           f.mental, f.health, f.willpower, f.exceptional, f.special
    FROM character_familiars cf
    LEFT JOIN familiars f ON f.id = cf.familiar_id
"""


def list_character_familiars(conn, character_id: int) -> list[dict]:
    """A character's bonded famuli, each merged with its catalog stat block."""
    rows = conn.execute(
        _CHAR_FAMILIAR_SELECT + " WHERE cf.character_id=? ORDER BY cf.created_at",
        (character_id,)).fetchall()
    return [_character_familiar_view(r) for r in rows]


def get_character_familiar(conn, bond_id: int) -> dict | None:
    return _character_familiar_view(
        conn.execute(_CHAR_FAMILIAR_SELECT + " WHERE cf.id=?", (bond_id,)).fetchone())


def get_character_familiar_for_player(conn, bond_id: int, discord_id: str) -> dict | None:
    """A bond row only if its character belongs to the player (ownership gate)."""
    return conn.execute(
        "SELECT cf.* FROM character_familiars cf "
        "JOIN characters ch ON ch.id = cf.character_id "
        "WHERE cf.id=? AND ch.discord_id=?", (bond_id, discord_id)).fetchone()


def bond_familiar(conn, *, character_id: int, familiar_id: int, name: str,
                  notes: str | None = None) -> dict | None:
    """Bond a catalog animal to a character as a named famulus."""
    cat = get_familiar(conn, familiar_id)
    if not cat:
        return None
    cur = conn.execute("""
        INSERT INTO character_familiars
            (character_id, familiar_id, animal_name, name, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (character_id, familiar_id, cat["name"], name, notes, _now()))
    return get_character_familiar(conn, cur.lastrowid)


def unbond_familiar(conn, bond_id: int) -> None:
    conn.execute("DELETE FROM character_familiars WHERE id=?", (bond_id,))


# ── Chronicle Settings ────────────────────────────────────────────────────────

def get_settings(conn) -> dict | None:
    row = conn.execute("SELECT * FROM chronicle_settings WHERE id=1").fetchone()
    return _parse(row, "revenant_families", "homebrew_tier_budgets",
                  "unlocked_predator_types")


def coterie_max_members(conn) -> int:
    """The chronicle's coterie member cap (migration 045) — a per-chronicle
    setting that falls back to the COTERIE_MAX_MEMBERS config constant when
    unset or invalid."""
    val = (get_settings(conn) or {}).get("coterie_max_members")
    try:
        val = int(val)
    except (TypeError, ValueError):
        val = 0
    return val if val > 0 else settings.COTERIE_MAX_MEMBERS


# Chronicle ruleset constants — gives the rest of the app a single place
# to look up valid values + the V5 RAW defaults.
RULESETS = ("standard", "homebrew")  # base budget rulesets. In Memoriam is no
# longer a mutually-exclusive ruleset value (migration 040) — it's an orthogonal
# `in_memoriam_enabled` flag layered on top of either base, so a chronicle can
# offer Standard AND In Memoriam and let Ancilla players choose.

# Per-tier budget defaults used when the chronicle is on the standard
# ruleset or hasn't customized that tier yet. Values reflect V5 RAW
# starting allotments for each character archetype.
#
# Kindred tiers map to V5's Sea of Time (Corebook p.130). The "xp" here is the
# STANDARD-ruleset finishing-touches pool the wizard hands the player — and the
# SINGLE source of truth: the wizard no longer layers any bonus on top, so the
# Sea-of-Time advantage of an older Kindred is baked straight into this number.
#   fledgling — Childer, embraced last 15 yr. Baseline: 0 finishing XP. BP 1.
#   thinblood — variant of Fledgling. 14th-16th gen, BP 0, no clan disciplines
#               (uses Alchemy instead). Plus 1-3 Thin-Blood Merits + matching
#               Thin-Blood Flaws on top of the standard 7 advantage / 2 flaw
#               allocation — Steward verifies at approval. Same 0 baseline.
#   neonate   — embraced 1940 to a decade ago. BP 1. 15 XP (V5 RAW finishing
#               touches — the standard starting vampire).
#   ancilla   — embraced 1780-1940. BP 2. 35 XP (Sea-of-Time bonus for age),
#               plus +2 advantages / +2 flaws / -1 Humanity over baseline.
#   mortal / ghoul / revenant — non-Kindred archetypes: 0 finishing XP.
#
# The "merits + advantages + backgrounds" total is the V5 RAW pool of
# 7 advantage points; the split into three buckets is for the wizard
# sidebar (combined-pool admin override since Steward UX revision).
_TIER_DEFAULTS = {
    "mortal":    {"xp": 0,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "ghoul":     {"xp": 0,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "revenant":  {"xp": 0,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    # Childer / Fledgling — baseline Kindred, no Sea of Time XP bonus.
    "fledgling": {"xp": 0,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    # Thin-Blood — same XP as Fledgling but uses Alchemy instead of
    # in-clan Disciplines. Steward verifies the 1-3 Thin-Blood
    # Merits/Flaws at approval — they don't eat the standard pool.
    "thinblood": {"xp": 0,  "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    "neonate":   {"xp": 15, "merits": 2, "advantages": 2, "backgrounds": 3, "flaw_cap": 2},
    # NYbN house rule: standard Ancilla take 3 Flaw dots (one of which must be
    # Archaic — enforced as chargen guidance + verified by staff at approval).
    "ancilla":   {"xp": 35, "merits": 3, "advantages": 3, "backgrounds": 3, "flaw_cap": 3},
}


def tier_budget(settings: dict | None, tier: str) -> dict:
    """Return the active budget for a tier.

    Per-tier overrides in homebrew_tier_budgets apply ONLY under the
    'homebrew' ruleset. Under 'standard' the function returns pure V5 RAW
    defaults and ignores any stored overrides — flipping to Standard is the
    "reset to RAW" switch. In Memoriam is orthogonal to budgets (it only adds
    the Ancilla Era-Builder choice), so it does NOT cause overrides to apply:
    a Standard + In Memoriam chronicle still gets RAW defaults here. Falls
    back to defaults for partial or missing overrides.

    NOTE: the staff admin per-tier-budget table is shown only under Homebrew
    for exactly this reason — keep that x-show in sync with this gating."""
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
        # XP cap on/off toggle (migration 027) + amount (migration 028)
        "xp_cap_enabled", "xp_cap_amount",
        # Per-player character cap (migration 032)
        "max_chars_per_player",
        # Project rolls per timeskip (migration 035)
        "rolls_per_timeskip",
        # In Memoriam decoupled from active_ruleset + chargen mode (migration 040)
        "in_memoriam_enabled", "creation_mode",
        # Chronicle-wide project mode toggle (migration 043)
        "project_mode",
        # Homebrew project engine: optional launch roll (migration 050)
        "homebrew_launch_roll",
        # Per-chronicle coterie member cap (migration 045)
        "coterie_max_members",
        # Web dice roller (Roll tab) on/off toggle (migration 051)
        "dice_roller_enabled",
    }
    # Back-compat: 'in_memoriam' was a discrete active_ruleset value before
    # migration 040. It's now an orthogonal flag — translate a legacy POST
    # (Standard base + In Memoriam on) so old callers and stored rows don't error.
    if kwargs.get("active_ruleset") == "in_memoriam":
        kwargs = dict(kwargs)
        kwargs["active_ruleset"] = "standard"
        kwargs.setdefault("in_memoriam_enabled", 1)
    # Validate ruleset before persisting — guard against typo'd POSTs.
    if "active_ruleset" in kwargs and kwargs["active_ruleset"] not in RULESETS:
        raise ValueError(f"Unknown ruleset: {kwargs['active_ruleset']!r}")
    if "creation_mode" in kwargs and kwargs["creation_mode"] not in ("guided", "open"):
        raise ValueError(f"Unknown creation_mode: {kwargs['creation_mode']!r}")
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


# ── Character backgrounds (V5 background blanking) ────────────────────────────
#
# A character tracks named backgrounds, each with a dot total. "Blanking" takes
# N dots out of play for one night; the dots auto-restore when the next play
# period opens. Re-keyed from the source tracker's integer night ordinal onto
# Enoch's period identity: a blank stores the period it was made in
# (blanked_period_id) and is "due" once a *different* period is active.

def _bg_key(name: str) -> str:
    """Slugify a background name into the per-character dedupe identity, so
    'High Society' and 'high  society!' collapse onto one tracked row."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")[:120]


def _bg_view(row: dict) -> dict:
    """Decorate a raw character_backgrounds row with computed totals."""
    total   = max(0, int(row.get("dots") or 0))
    blanked = max(0, min(int(row.get("blanked_dots") or 0), total))
    return {
        **row,
        "dots": total,
        "blanked_dots": blanked,
        "available": max(0, total - blanked),
        "is_blanked": blanked > 0,
    }


def list_character_backgrounds(conn, character_id: int) -> list[dict]:
    """All tracked backgrounds for a character, name-ordered, each decorated
    with computed total / blanked / available dots."""
    rows = conn.execute(
        "SELECT * FROM character_backgrounds WHERE character_id=? "
        "ORDER BY name COLLATE NOCASE",
        (character_id,),
    ).fetchall()
    return [_bg_view(r) for r in rows]


def set_character_background(
    conn, character_id: int, name: str, dots: int, updated_by: str = ""
) -> dict:
    """Create, update, or remove (dots=0) a tracked background. Lowering the
    total below the currently-blanked count re-clamps the blank; if that zeroes
    it, the pending release is cleared. Returns {'deleted': bool, 'name': str}."""
    name = (name or "").strip()[:120]
    if not name:
        raise ValueError("Background name is required.")
    key = _bg_key(name)
    if not key:
        raise ValueError("Background name must contain a letter or number.")
    total = max(0, int(dots))
    row = conn.execute(
        "SELECT * FROM character_backgrounds WHERE character_id=? AND bg_key=?",
        (character_id, key),
    ).fetchone()

    if row is None:
        if total == 0:
            return {"deleted": False, "name": name}
        conn.execute(
            "INSERT INTO character_backgrounds "
            "(character_id, name, bg_key, dots, blanked_dots, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, 0, ?, ?)",
            (character_id, name, key, total, _now(), updated_by),
        )
        return {"deleted": False, "name": name}

    if total == 0:
        conn.execute("DELETE FROM character_backgrounds WHERE id=?", (row["id"],))
        return {"deleted": True, "name": name}

    blanked = max(0, min(int(row["blanked_dots"] or 0), total))
    if blanked == 0:
        conn.execute(
            "UPDATE character_backgrounds SET name=?, dots=?, blanked_dots=0, "
            "blanked_period_id=NULL, blanked_at=NULL, updated_at=?, updated_by=? "
            "WHERE id=?",
            (name, total, _now(), updated_by, row["id"]),
        )
    else:
        conn.execute(
            "UPDATE character_backgrounds SET name=?, dots=?, blanked_dots=?, "
            "updated_at=?, updated_by=? WHERE id=?",
            (name, total, blanked, _now(), updated_by, row["id"]),
        )
    return {"deleted": False, "name": name}


def blank_character_background(
    conn, character_id: int, name: str, dots_to_blank: int, updated_by: str = ""
) -> dict:
    """Blank N dots of a tracked background for the current night. Requires an
    active play period. A leftover blank from an *earlier* period is released
    first so the one-night window never compounds across nights; same-night
    re-blanks accumulate. Raises ValueError on any invalid input."""
    key = _bg_key(name)
    if not key:
        raise ValueError("Background name is required.")
    active = get_active_period(conn)
    if not active:
        raise ValueError("There is no active play period — blanking needs an open night.")
    dots = int(dots_to_blank)
    if dots <= 0:
        raise ValueError("You must blank at least 1 dot.")

    row = conn.execute(
        "SELECT * FROM character_backgrounds WHERE character_id=? AND bg_key=?",
        (character_id, key),
    ).fetchone()
    if row is None:
        raise ValueError(f'"{name}" is not a tracked background.')

    total   = max(0, int(row["dots"] or 0))
    blanked = max(0, min(int(row["blanked_dots"] or 0), total))
    # A blank tied to a prior period is already due — drop it before re-blanking.
    if blanked > 0 and row["blanked_period_id"] != active["id"]:
        blanked = 0

    available = total - blanked
    if dots > available:
        raise ValueError(
            f"Cannot blank {dots} dot(s) of {row['name']}; only {available} available."
        )

    new_blanked = blanked + dots
    conn.execute(
        "UPDATE character_backgrounds SET blanked_dots=?, blanked_period_id=?, "
        "blanked_at=?, updated_at=?, updated_by=? WHERE id=?",
        (new_blanked, active["id"], _now(), _now(), updated_by, row["id"]),
    )
    return {
        "name":         row["name"],
        "dots":         total,
        "blanked_now":  dots,
        "blanked_dots": new_blanked,
        "available":    max(0, total - new_blanked),
        "period_label": active["label"],
    }


def release_due_background_blanks(conn) -> list[dict]:
    """Restore blanked dots whose blank predates the current active period (a
    new night has opened) and enqueue a `background_released` bot event for each
    owner. Returns the release events. No-op when no period is active."""
    active = get_active_period(conn)
    if not active:
        return []
    rows = conn.execute(
        """
        SELECT cb.*, c.discord_id AS owner_discord, c.name AS character_name
        FROM character_backgrounds cb
        JOIN characters c ON c.id = cb.character_id
        WHERE cb.blanked_dots > 0
          AND cb.blanked_period_id IS NOT NULL
          AND cb.blanked_period_id != ?
        """,
        (active["id"],),
    ).fetchall()
    released: list[dict] = []
    for row in rows:
        dots = int(row["blanked_dots"] or 0)
        if dots <= 0:
            continue
        conn.execute(
            "UPDATE character_backgrounds SET blanked_dots=0, blanked_period_id=NULL, "
            "blanked_at=NULL, updated_at=?, updated_by=? WHERE id=?",
            (_now(), "system:release", row["id"]),
        )
        event = {
            "discord_id":     row["owner_discord"],
            "character_id":   row["character_id"],
            "character_name": row["character_name"],
            "name":           row["name"],
            "dots_released":  dots,
        }
        enqueue_bot(conn, "background_released", event)
        released.append(event)
    return released


# ── Coterie shared-background blanking ───────────────────────────────────────
# A coterie's donated backgrounds form a shared pool. Any member can blank dots
# of one for the night, making them unavailable to the WHOLE coterie until the
# next play period — the same period-keyed release as the per-character feature
# above. The pool total per background is derived live from the active 'donated'
# contributions; coterie_background_blanks only tracks the blanks.

def list_coterie_shared_backgrounds(conn, coterie_id: int) -> list[dict]:
    """The coterie's donated backgrounds as a shared pool: each name with its
    total donated dots, how many are currently blanked, and what's available."""
    donated = conn.execute(
        """
        SELECT target_name AS name, SUM(dots) AS total
        FROM coterie_contributions
        WHERE coterie_id=? AND target_kind='background'
          AND contribution_type='donated' AND status='active'
          AND target_name IS NOT NULL AND TRIM(target_name) != ''
        GROUP BY target_name
        ORDER BY target_name COLLATE NOCASE
        """,
        (coterie_id,),
    ).fetchall()
    blanks = {
        r["bg_key"]: r
        for r in conn.execute(
            "SELECT * FROM coterie_background_blanks WHERE coterie_id=?",
            (coterie_id,),
        ).fetchall()
    }
    out: list[dict] = []
    for d in donated:
        name = d["name"]
        total = max(0, int(d["total"] or 0))
        b = blanks.get(_bg_key(name))
        blanked = max(0, min(int(b["blanked_dots"]) if b else 0, total))
        out.append({
            "name":         name,
            "dots":         total,
            "blanked_dots": blanked,
            "available":    max(0, total - blanked),
            "is_blanked":   blanked > 0,
        })
    return out


def blank_coterie_background(
    conn, coterie_id: int, name: str, dots_to_blank: int,
    blanked_by: int | None = None,
) -> dict:
    """Blank N dots of a coterie's shared background for the current night.
    Requires an active play period. A leftover blank from an earlier period is
    released first so the one-night window never compounds; same-night re-blanks
    accumulate. Raises ValueError on any invalid input."""
    key = _bg_key(name)
    if not key:
        raise ValueError("Background name is required.")
    active = get_active_period(conn)
    if not active:
        raise ValueError("There is no active play period — blanking needs an open night.")
    dots = int(dots_to_blank)
    if dots <= 0:
        raise ValueError("You must blank at least 1 dot.")

    shared = next(
        (b for b in list_coterie_shared_backgrounds(conn, coterie_id)
         if _bg_key(b["name"]) == key),
        None,
    )
    if shared is None:
        raise ValueError(f'"{name}" is not a shared coterie background.')
    total = shared["dots"]

    row = conn.execute(
        "SELECT * FROM coterie_background_blanks WHERE coterie_id=? AND bg_key=?",
        (coterie_id, key),
    ).fetchone()
    blanked = max(0, min(int(row["blanked_dots"] or 0), total)) if row else 0
    # A blank tied to a prior period is already due — drop it before re-blanking.
    if row and blanked > 0 and row["blanked_period_id"] != active["id"]:
        blanked = 0

    available = total - blanked
    if dots > available:
        raise ValueError(
            f"Cannot blank {dots} dot(s) of {shared['name']}; only {available} available."
        )

    new_blanked = blanked + dots
    if row:
        conn.execute(
            "UPDATE coterie_background_blanks SET name=?, blanked_dots=?, "
            "blanked_period_id=?, blanked_by=?, blanked_at=?, updated_at=? WHERE id=?",
            (shared["name"], new_blanked, active["id"], blanked_by,
             _now(), _now(), row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO coterie_background_blanks "
            "(coterie_id, name, bg_key, blanked_dots, blanked_period_id, "
            " blanked_by, blanked_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (coterie_id, shared["name"], key, new_blanked, active["id"],
             blanked_by, _now(), _now()),
        )
    return {
        "name":         shared["name"],
        "dots":         total,
        "blanked_now":  dots,
        "blanked_dots": new_blanked,
        "available":    max(0, total - new_blanked),
        "period_label": active["label"],
    }


def release_due_coterie_background_blanks(conn) -> list[dict]:
    """Restore coterie background blanks whose blank predates the current active
    period (a new night has opened). Returns the release events. No-op when no
    period is active."""
    active = get_active_period(conn)
    if not active:
        return []
    rows = conn.execute(
        "SELECT * FROM coterie_background_blanks WHERE blanked_dots > 0 "
        "AND blanked_period_id IS NOT NULL AND blanked_period_id != ?",
        (active["id"],),
    ).fetchall()
    released: list[dict] = []
    for row in rows:
        dots = int(row["blanked_dots"] or 0)
        if dots <= 0:
            continue
        conn.execute(
            "UPDATE coterie_background_blanks SET blanked_dots=0, "
            "blanked_period_id=NULL, blanked_at=NULL, updated_at=? WHERE id=?",
            (_now(), row["id"]),
        )
        released.append({
            "coterie_id":    row["coterie_id"],
            "name":          row["name"],
            "dots_released": dots,
        })
    return released


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
    """Deactivate all periods, then activate the given one. Opening a new night
    releases any background blanks that were made during a previous period."""
    conn.execute("UPDATE play_periods SET is_active=0")
    conn.execute("UPDATE play_periods SET is_active=1 WHERE id=?", (period_id,))
    release_due_background_blanks(conn)
    release_due_coterie_background_blanks(conn)
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

    # XP cap is chronicle-wide and optional (migration 027). When enabled
    # (default) award up to the per-character cap and open the retirement
    # window on first hit; when disabled award the full claim and never
    # auto-trigger retirement from the cap.
    settings   = get_settings(conn)
    cap_on     = bool((settings or {}).get("xp_cap_enabled", 1))
    cap_amount = int((settings or {}).get("xp_cap_amount", 350) or 350)
    if cap_on:
        # Creation XP is cap-exempt — only EARNED XP counts toward the cap.
        _earned  = char["xp_total"] - (char.get("creation_xp") or 0)
        cap_room = max(0, cap_amount - _earned)
        awarded  = min(claim["xp_claimed"], cap_room)
    else:
        awarded  = claim["xp_claimed"]

    now = _now()
    conn.execute("""
        UPDATE xp_claims SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?
    """, (reviewer_id, now, claim_id))

    if awarded > 0:
        new_total    = char["xp_total"] + awarded
        retirement   = char.get("retirement_eligible_at")
        if cap_on and (new_total - (char.get("creation_xp") or 0)) >= cap_amount and not retirement:
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
      Attribute / Skill (+ legacy "New Skill") -> attr_* / sk_* int
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
    if cat == "oblivion ceremony":
        _upsert_list("ceremonies", "level", int(new_dots), label)
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


# ── App alerts (operational warn/error log) ──────────────────────────────────
# Persisted warn/error entries from the web app (unhandled 500s) and the bot,
# surfaced on a dismissable staff page so silent failures get noticed.

_ALERT_LEVELS = {"warn", "error"}


def _insert_alert(conn, source: str, level: str, event: str, message: str,
                  detail: str = "") -> None:
    """Insert an alert via an EXISTING connection — use this when already inside
    a transaction (a fresh connection would deadlock on SQLite's write lock)."""
    conn.execute(
        "INSERT INTO app_alerts (source, level, event, message, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source if source in ("web", "bot") else "web",
         level if level in _ALERT_LEVELS else "error",
         (event or "")[:80], (message or "")[:500],
         (detail or "")[:8000], _now()),
    )


def log_alert(source: str, level: str, event: str, message: str,
              detail: str = "") -> None:
    """Persist an operational alert. Opens its own connection and never raises
    — it runs on error paths, so a logging failure must not mask the original
    error. Inside an open transaction, use `_insert_alert(conn, …)` instead."""
    try:
        with get_db() as conn:
            _insert_alert(conn, source, level, event, message, detail)
    except Exception:
        log.exception("Failed to persist app alert")


def list_alerts(conn, *, include_dismissed: bool = False, limit: int = 100) -> list[dict]:
    """Recent alerts — active (un-dismissed) first, then newest-first."""
    q = "SELECT * FROM app_alerts"
    if not include_dismissed:
        q += " WHERE dismissed_at IS NULL"
    q += " ORDER BY (dismissed_at IS NOT NULL), created_at DESC LIMIT ?"
    return conn.execute(q, (limit,)).fetchall()


def count_active_alerts(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM app_alerts WHERE dismissed_at IS NULL"
    ).fetchone()["n"]


def dismiss_alert(conn, alert_id: int, actor_id: str) -> None:
    conn.execute(
        "UPDATE app_alerts SET dismissed_at=?, dismissed_by=? "
        "WHERE id=? AND dismissed_at IS NULL",
        (_now(), actor_id, alert_id),
    )


def dismiss_all_alerts(conn, actor_id: str) -> int:
    cur = conn.execute(
        "UPDATE app_alerts SET dismissed_at=?, dismissed_by=? WHERE dismissed_at IS NULL",
        (_now(), actor_id),
    )
    return cur.rowcount


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


COTERIE_CREATION_STATES = {"forming", "submitted", "active"}


def create_coterie(
    conn,
    name: str,
    chasse: int = 0,
    lien: int = 0,
    portillon: int = 0,
    discord_role_id: str | None = None,
    creation_state: str = "active",
) -> dict:
    if creation_state not in COTERIE_CREATION_STATES:
        raise ValueError(f"Unknown creation_state: {creation_state!r}")
    now = _now()
    cur = conn.execute("""
        INSERT INTO coteries (name, chasse, lien, portillon, discord_role_id,
                              creation_state, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, chasse, lien, portillon, discord_role_id, creation_state, now, now))
    return get_coterie(conn, cur.lastrowid)


def submit_coterie_sheet(conn, coterie_id: int, actor_id: str) -> dict:
    """A member submits the assembled sheet for staff sign-off: forming →
    submitted. Members build the sheet (free dots / advantages / flaws) while
    forming; any member may submit once the group has agreed."""
    co = get_coterie(conn, coterie_id)
    if co is None:
        raise ValueError(f"Coterie {coterie_id} not found")
    if co["creation_state"] != "forming":
        raise ValueError(f"Coterie isn't forming (state: {co['creation_state']}).")
    conn.execute(
        "UPDATE coteries SET creation_state='submitted', updated_at=? WHERE id=?",
        (_now(), coterie_id),
    )
    write_audit(conn, actor_id, "submit_coterie_sheet", "coterie", coterie_id,
                after={"creation_state": "submitted"})
    return get_coterie(conn, coterie_id)


def approve_coterie_sheet(conn, coterie_id: int, reviewer_id: str) -> dict:
    """Staff sign-off: submitted → active. Creation-time allocation (free dots
    + the flaw budget) closes; further changes go through XP advances and
    donations. Notifies the members."""
    co = get_coterie(conn, coterie_id)
    if co is None:
        raise ValueError(f"Coterie {coterie_id} not found")
    if co["creation_state"] != "submitted":
        raise ValueError(f"Coterie isn't submitted (state: {co['creation_state']}).")
    conn.execute(
        "UPDATE coteries SET creation_state='active', updated_at=? WHERE id=?",
        (_now(), coterie_id),
    )
    write_audit(conn, reviewer_id, "approve_coterie_sheet", "coterie", coterie_id,
                after={"creation_state": "active"})
    for m in list_coterie_members(conn, coterie_id):
        ch = get_character(conn, m["character_id"])
        if ch and ch.get("discord_id"):
            enqueue_bot(conn, "coterie_sheet_approved", {
                "discord_id": ch["discord_id"],
                "coterie_name": co["name"], "coterie_id": coterie_id,
            })
    return get_coterie(conn, coterie_id)


def return_coterie_sheet(conn, coterie_id: int, reviewer_id: str, reason: str | None = None) -> dict:
    """Staff sends a submitted coterie back to forming for changes."""
    co = get_coterie(conn, coterie_id)
    if co is None:
        raise ValueError(f"Coterie {coterie_id} not found")
    if co["creation_state"] != "submitted":
        raise ValueError(f"Coterie isn't submitted (state: {co['creation_state']}).")
    conn.execute(
        "UPDATE coteries SET creation_state='forming', updated_at=? WHERE id=?",
        (_now(), coterie_id),
    )
    write_audit(conn, reviewer_id, "return_coterie_sheet", "coterie", coterie_id,
                after={"creation_state": "forming", "reason": reason})
    return get_coterie(conn, coterie_id)


def list_coteries_awaiting_signoff(conn) -> list[dict]:
    """Coteries that have been submitted and await staff sign-off."""
    return conn.execute("""
        SELECT co.*, COUNT(cm.id) AS member_count
        FROM coteries co
        LEFT JOIN coterie_memberships cm ON cm.coterie_id = co.id
        WHERE co.creation_state='submitted' AND co.status='active'
        GROUP BY co.id
        ORDER BY co.updated_at ASC
    """).fetchall()


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
        cap = coterie_max_members(conn)
        if count >= cap:
            raise ValueError(
                f"Coterie is full — max {cap} members."
            )
        # One character per player: a player can't have two of their own
        # characters in the same coterie.
        owner = conn.execute(
            "SELECT discord_id FROM characters WHERE id=?", (character_id,)
        ).fetchone()
        if owner and owner["discord_id"]:
            clash = conn.execute("""
                SELECT 1 FROM coterie_memberships cm
                JOIN characters c ON c.id = cm.character_id
                WHERE cm.coterie_id=? AND c.discord_id=? AND cm.character_id != ?
                LIMIT 1
            """, (coterie_id, owner["discord_id"], character_id)).fetchone()
            if clash:
                raise ValueError(
                    "That player already has a character in this coterie — "
                    "one character per player."
                )
    now = _now()
    conn.execute("""
        INSERT INTO coterie_memberships (coterie_id, character_id, role, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(coterie_id, character_id) DO UPDATE SET role=excluded.role
    """, (coterie_id, character_id, role, now))
    conn.execute("UPDATE coteries SET updated_at=? WHERE id=?", (now, coterie_id))
    return get_coterie(conn, coterie_id)


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


# ── Free creation dots ─────────────────────────────────────────────────────────
# At creation each member gets a small pool of free (no-XP) dots they can spend
# on ANY coterie trait — Domain (chasse/lien/portillon) or a named merit/
# background. Domain ratings are capped at 3 *at creation* (XP advances can push
# to 5 later); named traits cap at 3 like every other coterie trait.
CREATION_FREE_DOTS_PER_MEMBER = 2
COTERIE_DOMAIN_CREATION_CAP   = 3
COTERIE_FLAW_BONUS_CAP        = 4   # max extra Adv/Bg dots from flaws (1 per flaw dot)


def member_free_dots_used(conn, coterie_id: int, character_id: int) -> int:
    """Free creation dots a member has already committed to this coterie."""
    return conn.execute(
        "SELECT COALESCE(SUM(dots),0) AS n FROM coterie_contributions "
        "WHERE coterie_id=? AND character_id=? AND contribution_type='creation_free' "
        "AND status='active'",
        (coterie_id, character_id),
    ).fetchone()["n"]


def coterie_flaw_dots(conn, coterie_id: int) -> int:
    """Total active coterie flaw dots — each grants +1 creation dot."""
    return coterie_effective_rating(conn, coterie_id, "flaw")


def coterie_free_dots_used(conn, coterie_id: int) -> int:
    """All free creation dots committed to the coterie so far (across members)."""
    return conn.execute(
        "SELECT COALESCE(SUM(dots),0) AS n FROM coterie_contributions "
        "WHERE coterie_id=? AND contribution_type='creation_free' AND status='active'",
        (coterie_id,),
    ).fetchone()["n"]


def coterie_free_budget(conn, coterie_id: int) -> dict:
    """Creation-dot budget: 2 per member (base) + 1 per flaw dot (bonus, capped
    at COTERIE_FLAW_BONUS_CAP). Returns base/bonus/total/used/left/members."""
    members = list_coterie_members(conn, coterie_id)
    base  = CREATION_FREE_DOTS_PER_MEMBER * len(members)
    bonus = min(COTERIE_FLAW_BONUS_CAP, coterie_flaw_dots(conn, coterie_id))
    used  = coterie_free_dots_used(conn, coterie_id)
    total = base + bonus
    return {"base": base, "bonus": bonus, "total": total, "used": used,
            "left": max(0, total - used), "members": len(members)}


def commit_free_creation_dots(
    conn,
    *,
    coterie_id: int,
    character_id: int,
    target_kind: str,
    target_name: str | None,
    dots: int,
) -> dict:
    """Spend some of a member's free creation dots on a coterie trait. Free
    dots cost no XP and can go toward Domain (chasse/lien/portillon, capped at
    3 at creation) or a named merit/background (capped at 3). The coterie pool
    is 2 per member + 1 per flaw dot (flaw bonus). Raises ValueError on breach."""
    if dots < 1:
        raise ValueError("Allocate at least 1 dot.")
    if target_kind not in CONTRIBUTION_TARGET_KINDS or target_kind == "flaw":
        raise ValueError("Free dots go toward domain, a merit, or a background.")
    if target_kind in ("merit", "background"):
        target_name = (target_name or "").strip()
        if not target_name:
            raise ValueError("Name the merit or background.")
    else:
        target_name = None  # domain ratings aren't named at this layer

    members = list_coterie_members(conn, coterie_id)
    if not any(m["character_id"] == character_id for m in members):
        raise ValueError("Character is not a member of this coterie.")

    co = get_coterie(conn, coterie_id)
    if co is None or co["creation_state"] != "forming":
        raise ValueError("Free creation dots are only available while the coterie is forming.")

    budget = coterie_free_budget(conn, coterie_id)
    if budget["used"] + dots > budget["total"]:
        raise ValueError(
            f"Only {budget['left']} creation dot(s) left "
            f"({budget['base']} base + {budget['bonus']} flaw bonus)."
        )

    current = coterie_effective_rating(conn, coterie_id, target_kind, target_name)
    if target_kind in ("chasse", "lien", "portillon"):
        if current + dots > COTERIE_DOMAIN_CREATION_CAP:
            raise ValueError(
                f"{target_kind.title()} is capped at {COTERIE_DOMAIN_CREATION_CAP} at creation."
            )
    elif current + dots > COTERIE_NAMED_TRAIT_CAP:
        raise ValueError(f"\"{target_name}\" is capped at {COTERIE_NAMED_TRAIT_CAP} dots.")

    return add_coterie_contribution(
        conn,
        coterie_id=coterie_id,
        contribution_type="creation_free",
        target_kind=target_kind,
        target_name=target_name,
        dots=dots,
        character_id=character_id,
        note="Free creation dots",
    )


def commit_coterie_flaw(conn, *, coterie_id: int, flaw_name: str, dots: int) -> dict:
    """Add a coterie flaw during creation (forming). Flaws are coterie-wide;
    total flaw dots cap at COTERIE_FLAW_BONUS_CAP and each grants +1 Advantage/
    Background creation dot."""
    flaw_name = (flaw_name or "").strip()
    if not flaw_name:
        raise ValueError("Name the flaw.")
    if dots < 1:
        raise ValueError("A flaw is at least 1 dot.")
    co = get_coterie(conn, coterie_id)
    if co is None or co["creation_state"] != "forming":
        raise ValueError("Flaws can only be added while the coterie is forming.")
    current = coterie_flaw_dots(conn, coterie_id)
    if current + dots > COTERIE_FLAW_BONUS_CAP:
        raise ValueError(
            f"A coterie can take at most {COTERIE_FLAW_BONUS_CAP} flaw dots at creation "
            f"({COTERIE_FLAW_BONUS_CAP - current} left)."
        )
    return add_coterie_contribution(
        conn, coterie_id=coterie_id, contribution_type="flaw_bonus",
        target_kind="flaw", target_name=flaw_name, dots=dots,
        character_id=None, note="Creation flaw",
    )


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
    rows = conn.execute("""
        SELECT cr.*, hs.name AS requested_site_name
        FROM coterie_requests cr
        LEFT JOIN hunting_sites hs ON hs.id = cr.requested_site_id
        WHERE cr.status='pending'
        ORDER BY cr.submitted_at ASC
    """).fetchall()
    return [_parse(r, "member_ids") for r in rows]


def create_coterie_request(
    conn,
    requested_by: str,
    proposed_name: str,
    member_ids: list[int],
    note: str | None = None,
    members_acquainted: bool = False,
    requested_site_id: int | None = None,
) -> dict:
    cur = conn.execute("""
        INSERT INTO coterie_requests
            (requested_by, proposed_name, member_ids, note, submitted_at,
             members_acquainted, requested_site_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (requested_by, proposed_name, _j(member_ids), note, _now(),
          1 if members_acquainted else 0, requested_site_id))
    return get_coterie_request(conn, cur.lastrowid)


def approve_coterie_request(conn, request_id: int, reviewer_id: str) -> dict:
    req = get_coterie_request(conn, request_id)
    if req is None:
        raise ValueError(f"Coterie request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is not pending")

    member_ids = req["member_ids"] or []
    _cap = coterie_max_members(conn)
    if len(member_ids) > _cap:
        raise ValueError(
            f"Cannot approve — request has {len(member_ids)} members "
            f"(max {_cap})."
        )

    coterie = create_coterie(conn, req["proposed_name"], creation_state="forming")
    for char_id in member_ids:
        add_coterie_member(conn, coterie["id"], char_id)

    # Link the requested hunting site to the new coterie — but only if it
    # isn't already controlled, so approving one request can't silently
    # steal a site another coterie already holds.
    site_id = req.get("requested_site_id")
    if site_id:
        site_row = conn.execute(
            "SELECT coterie_id FROM hunting_sites WHERE id=?", (site_id,)
        ).fetchone()
        if site_row is not None and site_row["coterie_id"] is None:
            conn.execute(
                "UPDATE hunting_sites SET coterie_id=?, updated_at=? WHERE id=?",
                (coterie["id"], _now(), site_id),
            )

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


# ── Coterie member-add requests (migration 044) ──────────────────────────────
# A coterie leader proposes adding a character to their existing coterie; staff
# approve, which runs add_coterie_member. Mirrors the formation-request flow.

def get_coterie_member_request(conn, request_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM coterie_member_requests WHERE id=?", (request_id,)
    ).fetchone()
    return dict(row) if row else None


def has_pending_member_request(conn, coterie_id: int, character_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM coterie_member_requests "
        "WHERE coterie_id=? AND character_id=? AND status='pending' LIMIT 1",
        (coterie_id, character_id),
    ).fetchone() is not None


def list_pending_coterie_member_requests(conn) -> list[dict]:
    rows = conn.execute("""
        SELECT mr.*, co.name AS coterie_name,
               ch.name AS character_name, ch.clan AS character_clan,
               pp.username AS player_username
        FROM coterie_member_requests mr
        JOIN coteries        co ON co.id = mr.coterie_id
        JOIN characters      ch ON ch.id = mr.character_id
        LEFT JOIN player_profiles pp ON pp.discord_id = ch.discord_id
        WHERE mr.status='pending'
        ORDER BY mr.submitted_at ASC
    """).fetchall()
    return [dict(r) for r in rows]


def create_coterie_member_request(conn, coterie_id: int, character_id: int,
                                  requested_by: str, note: str | None = None) -> dict:
    cur = conn.execute("""
        INSERT INTO coterie_member_requests
            (coterie_id, character_id, requested_by, note, status, submitted_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (coterie_id, character_id, requested_by, note, _now()))
    return get_coterie_member_request(conn, cur.lastrowid)


def approve_coterie_member_request(conn, request_id: int, reviewer_id: str) -> dict:
    req = get_coterie_member_request(conn, request_id)
    if req is None:
        raise ValueError(f"Member request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is not pending")
    # add_coterie_member enforces the member cap + one-char-per-player; let it
    # raise so the staff route can surface the message.
    add_coterie_member(conn, req["coterie_id"], req["character_id"])
    conn.execute("""
        UPDATE coterie_member_requests
        SET status='approved', reviewed_by=?, reviewed_at=?
        WHERE id=?
    """, (reviewer_id, _now(), request_id))
    write_audit(conn, reviewer_id, "approve_coterie_member_request",
                "coterie_member_request", request_id,
                after={"coterie_id": req["coterie_id"],
                       "character_id": req["character_id"]})
    char    = get_character(conn, req["character_id"])
    coterie = get_coterie(conn, req["coterie_id"])
    if char and char.get("discord_id"):
        enqueue_bot(conn, "coterie_member_added", {
            "discord_id":   char["discord_id"],
            "coterie_name": coterie["name"] if coterie else "",
            "coterie_id":   req["coterie_id"],
        })
    return get_coterie_member_request(conn, request_id)


def reject_coterie_member_request(conn, request_id: int, reviewer_id: str,
                                  reason: str) -> dict:
    req = get_coterie_member_request(conn, request_id)
    if req is None:
        raise ValueError(f"Member request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"Request {request_id} is not pending")
    conn.execute("""
        UPDATE coterie_member_requests
        SET status='rejected', reviewed_by=?, reviewed_at=?, review_reason=?
        WHERE id=?
    """, (reviewer_id, _now(), reason, request_id))
    write_audit(conn, reviewer_id, "reject_coterie_member_request",
                "coterie_member_request", request_id, after={"reason": reason})
    enqueue_bot(conn, "coterie_member_request_rejected", {
        "discord_id": req["requested_by"],
        "reason":     reason,
    })
    return get_coterie_member_request(conn, request_id)


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


def _apply_coterie_chasse(conn, site: dict | None) -> dict | None:
    """A controlling coterie's Chasse lowers this site's hunting difficulties
    by 1 per dot (floored at 1) — V5: each Chasse dot eases feeding in the
    domain. Adds chasse_reduction, controlling_coterie, and effective_dcs
    (which equals the base DCs when the site is uncontrolled)."""
    if site is None:
        return None
    chasse, coterie_name = 0, None
    cid = site.get("coterie_id")
    if cid:
        co = conn.execute(
            "SELECT name, chasse FROM coteries WHERE id=? AND status='active'", (cid,)
        ).fetchone()
        if co:
            chasse = int(co["chasse"] or 0)
            coterie_name = co["name"]
    dcs = site.get("predator_dcs") or {}
    site["chasse_reduction"]    = chasse
    site["controlling_coterie"] = coterie_name
    site["effective_dcs"] = {
        pt: max(1, int(dc) - chasse)
        for pt, dc in dcs.items() if isinstance(dc, (int, float))
    }
    return site


def get_hunting_site(conn, site_id: int) -> dict | None:
    return _apply_coterie_chasse(conn, _enrich_site(
        conn.execute("SELECT * FROM hunting_sites WHERE id=?", (site_id,)).fetchone()
    ))


def list_hunting_sites(conn, active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM hunting_sites"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY borough, name"
    return [_apply_coterie_chasse(conn, _enrich_site(r))
            for r in conn.execute(sql).fetchall()]


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

# ── Projects (downtime endeavours) ────────────────────────────────────────────
#
# A character proposes a Project; staff approve it and pick how it runs (staged
# vs roll) and how it pays off (freeform vs structured). Staged projects advance
# by staff notes; roll projects are a V5 extended test the player rolls down once
# per period via the bot. Completion is always a staff action (which applies any
# payoff), keeping staff in approver mode.

def _project_view(row: dict | None) -> dict | None:
    """Decorate a project row: parse log_json/stages_json and add derived fields.
    Multi-stage roll projects (Phase A) carry a `stages_json` list of
    {label, dc, progress, done}; single-stage / legacy projects fall back to the
    flat target_successes counter."""
    if row is None:
        return None
    row = _parse(row, "log_json", "stages_json")
    if not isinstance(row.get("log_json"), list):
        row["log_json"] = []
    stages = row.get("stages_json")
    if not isinstance(stages, list):
        stages = []
    row["stages_json"] = stages
    row["is_coterie"] = row.get("coterie_id") is not None
    # Homebrew engine flags (migration 050) — harmless on NYbN projects.
    row["paused"]   = bool(row.get("paused"))
    row["launched"] = bool(row.get("launched", 1))

    if stages:
        row["target_reached"] = all(s.get("done") for s in stages)
        total_dc = sum(max(0, int(s.get("dc") or 0)) for s in stages) or 1
        done     = sum(min(int(s.get("progress") or 0), int(s.get("dc") or 0))
                       for s in stages)
        row["progress_pct"]  = min(100, round(100 * done / total_dc))
        row["stage_count"]   = len(stages)
        row["current_stage"] = max(0, min(int(row.get("current_stage") or 0),
                                          len(stages) - 1))
    else:
        target = int(row.get("target_successes") or 0)
        prog   = int(row.get("progress_successes") or 0)
        row["target_reached"] = (
            row.get("progress_type") == "roll" and target > 0 and prog >= target
        )
        row["progress_pct"] = min(100, round(100 * prog / target)) if target > 0 else 0
    return row


def get_project(conn, project_id: int) -> dict | None:
    return _project_view(
        conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    )


def list_projects_for_character(conn, character_id: int) -> list[dict]:
    """A character's *individual* projects only — coterie projects (coterie_id
    set) are owned by the coterie and listed via list_projects_for_coterie."""
    rows = conn.execute(
        "SELECT * FROM projects WHERE character_id=? AND coterie_id IS NULL "
        "ORDER BY created_at DESC",
        (character_id,),
    ).fetchall()
    return [_project_view(r) for r in rows]


def list_projects_for_coterie(conn, coterie_id: int) -> list[dict]:
    """A coterie's projects (Phase D). character_name is the proposer."""
    rows = conn.execute(
        """
        SELECT p.*, c.name AS character_name
        FROM projects p JOIN characters c ON c.id = p.character_id
        WHERE p.coterie_id=?
        ORDER BY p.created_at DESC
        """,
        (coterie_id,),
    ).fetchall()
    return [_project_view(r) for r in rows]


def list_pending_projects(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.*, c.name AS character_name, co.name AS coterie_name
        FROM projects p JOIN characters c ON c.id = p.character_id
        LEFT JOIN coteries co ON co.id = p.coterie_id
        WHERE p.status='proposed'
        ORDER BY p.created_at ASC
        """
    ).fetchall()
    return [_project_view(r) for r in rows]


def list_active_projects(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.*, c.name AS character_name, co.name AS coterie_name
        FROM projects p JOIN characters c ON c.id = p.character_id
        LEFT JOIN coteries co ON co.id = p.coterie_id
        WHERE p.status='active'
        ORDER BY p.updated_at DESC
        """
    ).fetchall()
    return [_project_view(r) for r in rows]


def list_recent_finished_projects(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.*, c.name AS character_name, co.name AS coterie_name
        FROM projects p JOIN characters c ON c.id = p.character_id
        LEFT JOIN coteries co ON co.id = p.coterie_id
        WHERE p.status IN ('complete','rejected')
        ORDER BY p.reviewed_at DESC, p.updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_project_view(r) for r in rows]


def _project_log_append(conn, project_id: int, entry: dict) -> None:
    proj = get_project(conn, project_id)
    if proj is None:
        return
    log = proj.get("log_json") or []
    log.append({"at": _now(), **entry})
    conn.execute(
        "UPDATE projects SET log_json=?, updated_at=? WHERE id=?",
        (_j(log), _now(), project_id),
    )


def create_project(conn, character_id: int, title: str, description: str,
                   proposed_by: str, coterie_id: int | None = None) -> dict:
    """Propose a project. coterie_id set => a Phase D coterie project owned by
    that coterie (character_id is the proposing member); None => an individual
    project owned by character_id."""
    title = (title or "").strip()[:120]
    if not title:
        raise ValueError("A project title is required.")
    cur = conn.execute(
        """
        INSERT INTO projects (character_id, coterie_id, title, description, status,
                              proposed_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)
        """,
        (character_id, coterie_id, title, (description or "").strip(),
         proposed_by, _now(), _now()),
    )
    return get_project(conn, cur.lastrowid)


def approve_project(conn, project_id: int, reviewer_id: str, *,
                    progress_type: str, payoff_type: str,
                    roll_pool: str = "", roll_difficulty: int = 1,
                    target_successes: int = 0,
                    stages: list | None = None,
                    reward_category: str | None = None,
                    reward_trait: str | None = None,
                    reward_dots: int = 0, reward_xp: int = 0,
                    launched: int = 1) -> dict:
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError(f"Project {project_id} not found")
    if proj["status"] != "proposed":
        raise ValueError("Only a proposed project can be approved.")
    if progress_type not in ("staged", "roll"):
        raise ValueError("Progress type must be 'staged' or 'roll'.")
    if payoff_type not in ("freeform", "structured"):
        raise ValueError("Payoff type must be 'freeform' or 'structured'.")
    norm_stages: list[dict] = []
    if progress_type == "roll":
        if not (roll_pool or "").strip():
            raise ValueError("A roll project needs a dice pool.")
        for s in (stages or []):
            dc = max(0, int(s.get("dc") or 0))
            if dc <= 0:
                continue
            norm_stages.append({
                "label": (s.get("label") or "").strip()[:80],
                "dc": dc, "progress": 0, "done": False,
            })
        # Back-compat: the old single-target form (a target with no explicit
        # stages) stays a flat counter handled by record_project_roll.
        if not norm_stages and int(target_successes) <= 0:
            raise ValueError("A roll project needs at least one stage with a DC.")
    total_target = (sum(s["dc"] for s in norm_stages) if norm_stages
                    else max(0, int(target_successes)))
    conn.execute(
        """
        UPDATE projects SET status='active', progress_type=?, payoff_type=?,
            roll_pool=?, roll_difficulty=?, target_successes=?,
            stages_json=?, current_stage=0, launched=?, paused=0,
            reward_category=?, reward_trait=?, reward_dots=?, reward_xp=?,
            reviewed_by=?, reviewed_at=?, updated_at=?
        WHERE id=?
        """,
        (progress_type, payoff_type, (roll_pool or "").strip(),
         max(0, int(roll_difficulty)), total_target, _j(norm_stages),
         int(bool(launched)),
         reward_category, reward_trait, max(0, int(reward_dots)),
         max(0, int(reward_xp)), reviewer_id, _now(), _now(), project_id),
    )
    _project_log_append(conn, project_id,
                        {"by": reviewer_id, "kind": "approved",
                         "text": f"{progress_type} / {payoff_type}"})
    write_audit(conn, reviewer_id, "approve_project", "project", project_id,
                after={"progress_type": progress_type, "payoff_type": payoff_type})
    char = get_character(conn, proj["character_id"])
    if char and char.get("discord_id"):
        enqueue_bot(conn, "project_approved", {
            "discord_id":    char["discord_id"],
            "project_id":    project_id,
            "project_name":  proj["title"],
            "progress_type": progress_type,
        })
    return get_project(conn, project_id)


def reject_project(conn, project_id: int, reviewer_id: str, reason: str) -> dict:
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError(f"Project {project_id} not found")
    if proj["status"] != "proposed":
        raise ValueError("Only a proposed project can be rejected.")
    conn.execute(
        "UPDATE projects SET status='rejected', reviewed_by=?, reviewed_at=?, "
        "updated_at=? WHERE id=?",
        (reviewer_id, _now(), _now(), project_id),
    )
    _project_log_append(conn, project_id,
                        {"by": reviewer_id, "kind": "rejected", "text": reason})
    write_audit(conn, reviewer_id, "reject_project", "project", project_id,
                after={"reason": reason})
    char = get_character(conn, proj["character_id"])
    if char and char.get("discord_id"):
        enqueue_bot(conn, "project_rejected", {
            "discord_id":   char["discord_id"],
            "project_id":   project_id,
            "project_name": proj["title"],
            "reason":       reason,
        })
    return get_project(conn, project_id)


def add_project_note(conn, project_id: int, staff_id: str, text: str) -> dict:
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError(f"Project {project_id} not found")
    if proj["status"] != "active":
        raise ValueError("Notes can only be added to an active project.")
    text = (text or "").strip()
    if not text:
        raise ValueError("Note text is required.")
    _project_log_append(conn, project_id, {"by": staff_id, "kind": "note", "text": text})
    return get_project(conn, project_id)


PROJECT_MODES = {"nybn", "homebrew", "raw", "off"}


def get_project_mode(conn) -> str:
    """Chronicle-wide project ruleset (migration 043). Defaults to 'nybn'."""
    mode = ((get_settings(conn) or {}).get("project_mode") or "nybn").strip().lower()
    return mode if mode in PROJECT_MODES else "nybn"


def projects_enabled(conn) -> bool:
    """False when the chronicle has Projects turned off."""
    return get_project_mode(conn) != "off"


def get_homebrew_launch_roll(conn) -> bool:
    """Whether the Homebrew engine requires a launch roll to open a project
    (migration 050). Off by default — some chronicles run it, some don't."""
    return bool((get_settings(conn) or {}).get("homebrew_launch_roll", 0))


def get_rolls_per_timeskip(conn) -> int:
    """The chronicle-wide project-roll budget each character gets per timeskip."""
    s = get_settings(conn) or {}
    try:
        return max(0, int(s.get("rolls_per_timeskip", 8) or 8))
    except (TypeError, ValueError):
        return 8


def get_timeskip_rolls_used(conn, character_id: int, period_id: int) -> int:
    row = conn.execute(
        "SELECT rolls_used FROM timeskip_roll_usage "
        "WHERE character_id=? AND period_id=?",
        (character_id, period_id),
    ).fetchone()
    return int(row["rolls_used"]) if row else 0


def timeskip_rolls_remaining(conn, character_id: int) -> dict:
    """{used, cap, remaining, period_id} for the active period. remaining=0 and
    period_id=None when no period is active."""
    cap    = get_rolls_per_timeskip(conn)
    active = get_active_period(conn)
    if not active:
        return {"used": 0, "cap": cap, "remaining": 0, "period_id": None}
    used = get_timeskip_rolls_used(conn, character_id, active["id"])
    return {"used": used, "cap": cap, "remaining": max(0, cap - used),
            "period_id": active["id"]}


def consume_timeskip_roll(conn, character_id: int, period_id: int) -> None:
    conn.execute(
        """
        INSERT INTO timeskip_roll_usage (character_id, period_id, rolls_used)
        VALUES (?, ?, 1)
        ON CONFLICT(character_id, period_id)
        DO UPDATE SET rolls_used = rolls_used + 1
        """,
        (character_id, period_id),
    )


# ── Downtime actions (spend a timeskip roll — hunting, etc.) ──────────────────

def log_downtime_action(conn, character_id: int, period_id: int | None,
                        kind: str, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO downtime_actions (character_id, period_id, kind, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (character_id, period_id, kind, note, _now()))


def list_downtime_actions(conn, character_id: int, period_id: int,
                          kind: str | None = None) -> list[dict]:
    sql = ("SELECT * FROM downtime_actions WHERE character_id=? AND period_id=?"
           + (" AND kind=?" if kind else "") + " ORDER BY created_at")
    args = (character_id, period_id, kind) if kind else (character_id, period_id)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def hunt_downtime(conn, character_id: int, note: str | None = None) -> dict:
    """Spend one timeskip roll to hunt this period. Generic for now — decrements
    the shared roll budget and logs the action; the actual outcome is ST/bot
    resolved. Returns {ok, error, remaining}."""
    rolls = timeskip_rolls_remaining(conn, character_id)
    if not rolls["period_id"]:
        return {"ok": False, "error": "No active timeskip right now.", "remaining": 0}
    if rolls["remaining"] < 1:
        return {"ok": False, "error": "No project rolls left this timeskip.",
                "remaining": 0}
    consume_timeskip_roll(conn, character_id, rolls["period_id"])
    log_downtime_action(conn, character_id, rolls["period_id"], "hunt", note)
    return {"ok": True, "error": None, "remaining": rolls["remaining"] - 1}


def record_project_roll(conn, project_id: int, *, successes: int, outcome: str,
                        period_id: int | None,
                        actor_character_id: int | None = None) -> dict:
    """Accumulate a downtime roll's successes toward a roll project's target.
    Returns the updated project; the caller reads `target_reached`. Raises
    ValueError on any invalid state.

    `actor_character_id` is the character actually rolling — for a coterie
    project (Phase D) any member may roll and the roll is charged to that
    member's own timeskip budget. Defaults to the project's owner."""
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError("Project not found.")
    if proj["status"] != "active" or proj.get("progress_type") != "roll":
        raise ValueError("This project isn't an active roll project.")
    if period_id is None:
        raise ValueError("There is no active play period to roll in.")
    # NYbN: each character has a shared pool of project rolls per timeskip
    # (not one per project). The roller spends from their OWN pool.
    roller   = actor_character_id or proj["character_id"]
    cap  = get_rolls_per_timeskip(conn)
    used = get_timeskip_rolls_used(conn, roller, period_id)
    if used >= cap:
        raise ValueError(f"No project rolls left this timeskip ({used}/{cap} used).")
    consume_timeskip_roll(conn, roller, period_id)
    gain = max(0, int(successes))
    new_prog = int(proj["progress_successes"] or 0) + gain
    conn.execute(
        "UPDATE projects SET progress_successes=?, last_roll_period_id=?, "
        "updated_at=? WHERE id=?",
        (new_prog, period_id, _now(), project_id),
    )
    _project_log_append(conn, project_id,
                        {"by": "player", "kind": "roll", "char": _project_roller_name(conn, proj, roller),
                         "successes": gain, "outcome": outcome, "progress": new_prog})
    return get_project(conn, project_id)


def _project_roller_name(conn, proj: dict, roller_id: int) -> str | None:
    """For a coterie project, the name of the member who rolled (so the staff
    log can attribute each roll). None for an individual project."""
    if not proj.get("coterie_id"):
        return None
    ch = get_character(conn, roller_id)
    return ch.get("name") if ch else None


def resolve_project_roll(conn, project_id: int, *, successes: int,
                         critical: bool = False, messy: bool = False,
                         hunger_one: bool = False, pool_size: int = 0,
                         period_id: int | None,
                         actor_character_id: int | None = None) -> dict:
    """Apply one downtime roll to a multi-stage roll project's *current* stage,
    consuming one timeskip roll. Handles cumulative progress, stage completion
    with overflow spill into the next stage, and the crit/messy/bestial outcomes
    (engine math only — staff apply the narrative flaws / penalties / temp dots).

    Outcome rules (see docs/NYBN_DOWNTIME_PROJECTS.md, with flagged assumptions):
      * progress accumulates toward the current stage's DC across rolls;
      * on completion, leftover successes spill into the next stage — full for a
        normal/crit completion, half for a messy crit; a final-stage completion
        flags staff for temporary background dots on crit/messy;
      * a project-bestial (a Hunger 1 AND successes < ceil(stage DC / 10)) banks
        no progress, raises that stage's DC by half the dice pool, and flags a
        staff penalty.

    Returns {project, result}; `result` describes the outcome for the bot to
    render. Raises ValueError on any invalid state."""
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError("Project not found.")
    if proj["status"] != "active" or proj.get("progress_type") != "roll":
        raise ValueError("This project isn't an active roll project.")
    stages = proj.get("stages_json") or []
    if not stages:
        raise ValueError("This project has no stages to roll against.")
    if period_id is None:
        raise ValueError("There is no active play period to roll in.")
    # The roller spends from their own per-character timeskip budget; for a
    # coterie project (Phase D) that's whichever member is rolling tonight.
    roller = actor_character_id or proj["character_id"]
    cap  = get_rolls_per_timeskip(conn)
    used = get_timeskip_rolls_used(conn, roller, period_id)
    if used >= cap:
        raise ValueError(f"No project rolls left this timeskip ({used}/{cap} used).")
    consume_timeskip_roll(conn, roller, period_id)

    idx   = max(0, min(int(proj.get("current_stage") or 0), len(stages) - 1))
    stage = stages[idx]
    dc    = int(stage.get("dc") or 0)
    succ  = max(0, int(successes))
    pool  = max(0, int(pool_size))
    new_current = idx
    flags: list[str] = []

    if hunger_one and succ < (dc + 9) // 10:          # ceil(dc/10) -> bestial
        # The successes still bank, then the stage DC rises by half the pool and
        # a penalty is flagged for staff.
        stage["progress"] = int(stage.get("progress") or 0) + succ
        stage["dc"] = dc + (pool // 2)
        flags.append("bestial")
        result = {"outcome": "bestial", "stage": idx + 1,
                  "stage_dc": stage["dc"], "gained": succ}
    else:
        stage["progress"] = int(stage.get("progress") or 0) + succ
        if stage["progress"] >= dc:
            overflow = stage["progress"] - dc
            stage["progress"] = dc
            stage["done"] = True
            # Only a crit (full) or messy crit (half) carries overflow into the
            # next stage; a plain success that clears the DC loses the leftover.
            if critical:
                carry = overflow
                flags.append("crit")
            elif messy:
                carry = overflow // 2
                flags.append("messy")
            else:
                carry = 0
            if idx + 1 < len(stages):
                nxt = stages[idx + 1]
                nxt["progress"] = int(nxt.get("progress") or 0) + carry
                new_current = idx + 1
                result = {"outcome": "stage_complete", "stage": idx + 1,
                          "carry": carry, "next_stage": idx + 2}
            else:
                if critical or messy:
                    flags.append("final_temp_dots")
                result = {"outcome": "project_complete", "stage": idx + 1, "carry": 0}
        else:
            result = {"outcome": "progress", "stage": idx + 1,
                      "gained": succ, "remaining": dc - stage["progress"]}

    result["flags"] = flags
    conn.execute(
        "UPDATE projects SET stages_json=?, current_stage=?, updated_at=? WHERE id=?",
        (_j(stages), new_current, _now(), project_id),
    )
    _project_log_append(conn, project_id, {
        "by": "player", "kind": "roll", "successes": succ,
        "char": _project_roller_name(conn, proj, roller),
        "outcome": result["outcome"], "stage": idx + 1, "flags": flags,
    })
    return {"project": get_project(conn, project_id), "result": result}


def resolve_homebrew_roll(conn, project_id: int, *, successes: int,
                          critical: bool = False, messy: bool = False,
                          hunger_one: bool = False, pool_size: int = 0,
                          period_id: int | None,
                          actor_character_id: int | None = None) -> dict:
    """Apply one downtime roll under the Homebrew engine (project_mode='homebrew').

    A single staff-set goal DC (target_successes) cumulative extended test — no
    stages. Two homebrew-specific rules:
      * if the project still needs a launch roll (launched=0), THIS roll opens it
        — any success launches the test; a plain failure just retries next
        timeskip;
      * a messy crit or a bestial failure (a Hunger-die 1 with zero successes)
        PAUSES the project and flags the ST (an alert), instead of NYbN's DC
        auto-bump. Normal successes bank toward the goal; a plain failure makes
        no progress. Returns {project, result}; raises ValueError on bad state."""
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError("Project not found.")
    if proj["status"] != "active" or proj.get("progress_type") != "roll":
        raise ValueError("This project isn't an active roll project.")
    if proj.get("paused"):
        raise ValueError("This project is paused for ST review.")
    if period_id is None:
        raise ValueError("There is no active play period to roll in.")
    roller = actor_character_id or proj["character_id"]
    cap  = get_rolls_per_timeskip(conn)
    used = get_timeskip_rolls_used(conn, roller, period_id)
    if used >= cap:
        raise ValueError(f"No project rolls left this timeskip ({used}/{cap} used).")
    consume_timeskip_roll(conn, roller, period_id)

    succ    = max(0, int(successes))
    bestial = bool(hunger_one) and succ == 0
    char    = _project_roller_name(conn, proj, roller)

    def _pause(phase_note: str, extra_set: str = "", extra_args: tuple = ()):
        conn.execute(
            f"UPDATE projects SET paused=1{extra_set}, updated_at=? WHERE id=?",
            (*extra_args, _now(), project_id))
        _insert_alert(conn, "web", "warn", "project_needs_st",
                      f"Project “{proj['title']}” paused on a "
                      f"{'bestial' if bestial else 'messy'} result",
                      f"{char or 'A player'} needs ST review — clear the pause to "
                      "let them resume.")
        _project_log_append(conn, project_id,
                            {"by": "player", "kind": "roll", "char": char,
                             "outcome": "paused", "phase": phase_note})

    # ── Launch roll — opens the test (only when homebrew_launch_roll is on) ──
    if not proj.get("launched"):
        if messy or bestial:
            _pause("launch")
        elif succ > 0:
            conn.execute("UPDATE projects SET launched=1, updated_at=? WHERE id=?",
                         (_now(), project_id))
            _project_log_append(conn, project_id,
                                {"by": "player", "kind": "roll", "char": char,
                                 "successes": succ, "outcome": "launched", "phase": "launch"})
        else:
            _project_log_append(conn, project_id,
                                {"by": "player", "kind": "roll", "char": char,
                                 "successes": succ, "outcome": "launch_failed", "phase": "launch"})
        outcome = ("paused" if (messy or bestial) else
                   ("launched" if succ > 0 else "launch_failed"))
        return {"project": get_project(conn, project_id),
                "result": {"outcome": outcome, "phase": "launch"}}

    # ── Extended test — bank successes toward the goal DC ──
    target   = max(0, int(proj.get("target_successes") or 0))
    new_prog = int(proj.get("progress_successes") or 0) + succ
    if messy or bestial:
        _pause("test", ", progress_successes=?, last_roll_period_id=?",
               (new_prog, period_id))
        outcome = "paused"
    else:
        conn.execute(
            "UPDATE projects SET progress_successes=?, last_roll_period_id=?, "
            "updated_at=? WHERE id=?", (new_prog, period_id, _now(), project_id))
        outcome = ("goal_reached" if (target > 0 and new_prog >= target)
                   else ("progress" if succ > 0 else "no_progress"))
        _project_log_append(conn, project_id,
                            {"by": "player", "kind": "roll", "char": char,
                             "successes": succ, "outcome": outcome, "progress": new_prog})
    return {"project": get_project(conn, project_id),
            "result": {"outcome": outcome, "gained": succ,
                       "progress": new_prog, "target": target}}


def set_project_paused(conn, project_id: int, paused: bool, actor_id: str) -> dict:
    """Pause / resume a project (the ST clears a homebrew 'needs review' pause)."""
    conn.execute("UPDATE projects SET paused=?, updated_at=? WHERE id=?",
                 (int(bool(paused)), _now(), project_id))
    _project_log_append(conn, project_id,
                        {"by": actor_id, "kind": "paused" if paused else "resumed"})
    return get_project(conn, project_id)


def complete_project(conn, project_id: int, staff_id: str,
                     reward_text: str | None = None) -> dict:
    """Mark a project complete and apply its payoff. 'freeform' records the
    staff-written outcome; 'structured' grants the configured dots and/or XP
    onto the sheet. Guards on active status."""
    proj = get_project(conn, project_id)
    if proj is None:
        raise ValueError(f"Project {project_id} not found")
    if proj["status"] != "active":
        raise ValueError("Only an active project can be completed.")
    char = get_character(conn, proj["character_id"])
    if char is None:
        raise ValueError("Project's character no longer exists.")

    granted: list[str] = []
    if proj.get("payoff_type") == "structured" and not proj.get("payoff_applied"):
        dots  = int(proj.get("reward_dots") or 0)
        cat   = proj.get("reward_category")
        trait = proj.get("reward_trait")
        if dots > 0 and cat and trait:
            sheet = dict(char.get("sheet_json") or {})
            sheet = _apply_spend_to_sheet(sheet, category=cat, trait_name=trait,
                                          new_dots=dots)
            conn.execute("UPDATE characters SET sheet_json=?, updated_at=? WHERE id=?",
                         (_j(sheet), _now(), char["id"]))
            granted.append(f"{trait} {dots} ({cat})")
        xp = int(proj.get("reward_xp") or 0)
        if xp > 0:
            adjust_xp_manual(conn, char["id"], xp,
                             f"Project reward: {proj['title']}", staff_id,
                             target="total")
            granted.append(f"+{xp} XP")
        conn.execute("UPDATE projects SET payoff_applied=1 WHERE id=?", (project_id,))

    final_text = (reward_text or "").strip() or proj.get("reward_text") or ""
    conn.execute(
        "UPDATE projects SET status='complete', reward_text=?, reviewed_by=?, "
        "reviewed_at=?, updated_at=? WHERE id=?",
        (final_text, staff_id, _now(), _now(), project_id),
    )
    summary = final_text or (", ".join(granted) if granted else "Project completed.")
    _project_log_append(conn, project_id,
                        {"by": staff_id, "kind": "completed", "text": summary})
    write_audit(conn, staff_id, "complete_project", "project", project_id,
                after={"granted": granted, "reward_text": final_text})
    if char.get("discord_id"):
        enqueue_bot(conn, "project_completed", {
            "discord_id":   char["discord_id"],
            "project_id":   project_id,
            "project_name": proj["title"],
            "reward":       summary,
        })
    return get_project(conn, project_id)


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
