"""db.py — Database connection, migrations, and query helpers.

Uses libsql-experimental which mirrors the sqlite3 API for local files
and speaks HTTP to Turso for production.
"""
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import libsql_experimental as libsql

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
        statements = [s.strip() for s in sql.split(";") if s.strip()]
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


# ── Characters ────────────────────────────────────────────────────────────────

def _enrich_char(row: dict | None) -> dict | None:
    row = _parse(row, "sheet_json")
    if row:
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


def list_characters(conn, status: str | None = None, clan: str | None = None) -> list[dict]:
    """Staff: all characters, optionally filtered."""
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if clan:
        clauses.append("clan=?")
        params.append(clan)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM characters {where} ORDER BY name", params
    ).fetchall()
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
) -> dict:
    now = _now()
    cur = conn.execute("""
        INSERT INTO characters
            (discord_id, name, clan, predator_type, concept, sire, covenant,
             sheet_json, has_ingrained_flaw, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        discord_id, name, clan, predator_type, concept, sire, covenant,
        _j(sheet_json or {}), int(has_ingrained_flaw), now, now,
    ))
    return get_character(conn, cur.lastrowid)


def update_character(conn, character_id: int, **fields) -> dict:
    """Update whitelisted character fields."""
    ALLOWED = {
        "name", "clan", "predator_type", "concept", "sire", "covenant",
        "sheet_json", "status", "is_approved", "approved_by", "approved_at",
        "retirement_eligible_at", "has_ingrained_flaw", "ingrained_xp_used",
        "profile_image_url", "profile_blurb",
    }
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
        SET is_approved=1, status='active', approved_by=?, approved_at=?, updated_at=?
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
    # Reset to pending so player can resubmit
    conn.execute(
        "UPDATE characters SET status='pending', updated_at=? WHERE id=?", (now, character_id)
    )
    char = get_character(conn, character_id)
    write_audit(conn, reviewer_id, "reject_character", "character", character_id,
                after={"status": "pending", "reason": reason})
    enqueue_bot(conn, "character_rejected", {
        "character_id": character_id,
        "discord_id": char["discord_id"],
        "reason": reason,
    })
    return char


# ── Chronicle Settings ────────────────────────────────────────────────────────

def get_settings(conn) -> dict | None:
    return conn.execute("SELECT * FROM chronicle_settings WHERE id=1").fetchone()


def upsert_settings(conn, **kwargs) -> dict:
    ALLOWED = {
        "server_start_date", "xp_frequency", "night_start_hour",
        "timeskip_interval", "midnight_split",
    }
    safe = {k: v for k, v in kwargs.items() if k in ALLOWED}
    if not safe:
        return get_settings(conn)
    safe["updated_at"] = _now()
    if get_settings(conn) is None:
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
    return get_settings(conn)


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


def create_claim(
    conn,
    character_id: int,
    play_period_id: int,
    claimed_criteria: list[dict],
    rp_links: list[str],
    path: str = "none",
    helper_note: str | None = None,
    staff_claim_conflict: bool = False,
) -> dict:
    xp_claimed = sum(c.get("xp_value_at_submission", 0) for c in claimed_criteria)
    cur = conn.execute("""
        INSERT INTO xp_claims
            (character_id, play_period_id, claimed_criteria, rp_links,
             path, helper_note, staff_claim_conflict, xp_claimed, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        character_id, play_period_id,
        _j(claimed_criteria), _j(rp_links),
        path, helper_note, int(staff_claim_conflict),
        xp_claimed, _now(),
    ))
    return get_claim(conn, cur.lastrowid)


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
) -> dict:
    cur = conn.execute("""
        INSERT INTO spend_requests
            (character_id, category, trait_name, current_dots, new_dots,
             verified_cost, is_ingrained, humanity_conditions, note, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        character_id, category, trait_name, current_dots, new_dots,
        verified_cost, int(is_ingrained),
        _j(humanity_conditions) if humanity_conditions else None,
        note, _now(),
    ))
    return get_spend(conn, cur.lastrowid)


def approve_spend(conn, spend_id: int, reviewer_id: str) -> dict:
    spend = get_spend(conn, spend_id)
    if spend is None:
        raise ValueError(f"Spend {spend_id} not found")
    if spend["status"] != "pending":
        raise ValueError(f"Spend {spend_id} is not pending (status: {spend['status']})")

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
    limit: int = 50,
) -> list[dict]:
    clauses, params = [], []
    if target_type:
        clauses.append("target_type=?")
        params.append(target_type)
    if target_id is not None:
        clauses.append("target_id=?")
        params.append(target_id)
    if actor_id:
        clauses.append("actor_id=?")
        params.append(actor_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    return conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY created_at DESC LIMIT ?", params
    ).fetchall()


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
    return conn.execute(
        "SELECT * FROM coteries WHERE status=? ORDER BY name", (status,)
    ).fetchall()


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
    conn.execute("UPDATE coteries SET updated_at=? WHERE id=?", (_now(), coterie_id))


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

    coterie = create_coterie(conn, req["proposed_name"])
    for char_id in (req["member_ids"] or []):
        add_coterie_member(conn, coterie["id"], char_id)

    now = _now()
    conn.execute("""
        UPDATE coterie_requests
        SET status='approved', reviewed_by=?, reviewed_at=?, coterie_id=?
        WHERE id=?
    """, (reviewer_id, now, coterie["id"], request_id))

    write_audit(conn, reviewer_id, "approve_coterie_request", "coterie_request", request_id,
                after={"coterie_id": coterie["id"]})
    return get_coterie_request(conn, request_id)


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
