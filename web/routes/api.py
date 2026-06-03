"""api.py — Bot service token API.

All routes under /api/* require `Authorization: Bearer <BOT_SERVICE_TOKEN>`.
The Discord bot is the only intended consumer — it never writes to the DB
directly; every mutation goes through these endpoints so the web layer stays
the single authority for validation and persistence.

Design rules:
  - Outbox pattern: web enqueues commands, bot drains + acks.
  - Read endpoints return plain dicts (FastAPI serializes to JSON).
  - Write endpoints use Pydantic request models for validation.
  - No HTML — all responses are JSON.
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..db import (
    HUNT_OUTCOMES,
    ack_outbox,
    blank_character_background,
    create_character,
    create_hunt_log,
    drain_outbox,
    get_active_period,
    get_character,
    get_project,
    list_character_backgrounds,
    list_projects_for_character,
    record_project_roll,
    resolve_project_roll,
    timeskip_rolls_remaining,
    get_coterie_for_character,
    get_db,
    get_hunting_site,
    get_player,
    list_characters,
    list_claims_for_character,
    list_coterie_members,
    list_hunting_sites,
    list_player_characters,
    list_spends_for_character,
    list_upcoming_periods,
    upsert_player,
    write_audit,
    update_character,
    STAFF_ROLES,
    get_staff_role,
    set_staff_role,
    staff_role_has_permission,
    list_all_players,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_bot(authorization: str | None = Header(default=None)) -> None:
    """Reject requests that don't carry the bot service token."""
    if not settings.BOT_SERVICE_TOKEN:
        raise HTTPException(status_code=503, detail="Bot API not configured")
    if authorization != f"Bearer {settings.BOT_SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid service token")


# ── Request models ────────────────────────────────────────────────────────────

class CharacterIn(BaseModel):
    discord_id: str
    name: str
    clan: str
    predator_type: str | None = None
    concept: str | None = None
    sire: str | None = None
    covenant: str | None = None
    has_ingrained_flaw: bool = False
    username: str | None = None       # used to upsert player profile simultaneously


class PlayerUpsertIn(BaseModel):
    username: str
    cubby_channel: str | None = None   # Discord channel ID for DMs


class StaffRoleIn(BaseModel):
    actor_discord_id: str                 # the staff member issuing the change
    target_discord_id: str                # who's being assigned
    target_username: str | None = None    # upsert the target's profile if new
    role: str | None = None               # one of STAFF_ROLES, or null to revoke


class AckIn(BaseModel):
    success: bool = True
    error: str | None = None


class CharacterStatusIn(BaseModel):
    status: str = Field(..., pattern="^(active|retired|dead)$")
    note: str | None = None            # audit note (e.g. "died in Session 12")


class DamageDeltaIn(BaseModel):
    """Delta updates to a character's damage tracks. All fields optional.

    A dice bot pushes deltas — never absolutes — so two concurrent rolls
    can both apply without one clobbering the other. Negative values are
    allowed (healing).
    """
    damage_health_sup:    int = 0
    damage_health_agg:    int = 0
    damage_willpower_sup: int = 0
    damage_willpower_agg: int = 0
    hunger:               int = 0   # delta against current hunger (0..5)
    humanity:             int = 0   # delta against current humanity (0..10)
    source: str | None = None       # optional label for audit (e.g. "dice:bot")


class MacroIn(BaseModel):
    """A named roll macro saved on a character's sheet for the dice bot.

    ``expression`` is a pool string (e.g. "strength + brawl"). Pass it empty /
    null to delete the macro.
    """
    name:       str = Field(..., min_length=1, max_length=40)
    expression: str | None = None


class ConditionIn(BaseModel):
    """A transient status/condition on a character (torpor, on fire, in
    frenzy, staked, …). ``active=False`` clears the named condition."""
    name:   str = Field(..., min_length=1, max_length=40)
    note:   str | None = None
    active: bool = True


class BondIn(BaseModel):
    """A blood bond this character holds toward a ``regnant`` (1-6 dots; 3 is a
    full bond — 3 drinks on separate nights within a year — and 6 is the max).
    Pass ``delta`` for a relative change (e.g. +1 per drink) or ``level`` to set
    it absolutely. A resulting level of 0 clears the bond."""
    regnant: str = Field(..., min_length=1, max_length=60)
    level:   int | None = None
    delta:   int | None = None


class BackgroundBlankIn(BaseModel):
    """Blank ``dots`` of a tracked background for the current night (the bot's
    `/blank` command). The background must already be tracked on the web sheet."""
    name: str = Field(..., min_length=1, max_length=120)
    dots: int = Field(default=1, ge=1, le=10)


class ProjectRollIn(BaseModel):
    """Record a downtime extended-test roll for a roll-type project (the bot's
    `/project roll`). The bot owns the dice engine, rolls, and posts the result;
    the web validates ownership + the timeskip budget and resolves it against the
    project's current stage."""
    requester_discord_id: str
    successes:  int  = Field(..., ge=0)
    outcome:    str  = Field(default="", max_length=40)
    critical:   bool = False
    messy:      bool = False
    hunger_one: bool = False
    pool_size:  int  = Field(default=0, ge=0)


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    """Health check — no auth required."""
    db_ok = False
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception as exc:
        log.warning("Health check DB error: %s", exc)
    return {"ok": db_ok, "service": "enoch"}


# ── Outbox — drain / ack ──────────────────────────────────────────────────────

@router.get("/outbox", dependencies=[Depends(_require_bot)])
async def drain(limit: int = 10):
    """
    Fetch and lock pending bot commands (marks them 'processing').
    The bot should call this on a polling schedule, process each command,
    then call POST /api/outbox/{id}/ack for each one.

    Returns: { items: [...], count: N }
    """
    with get_db() as conn:
        items = drain_outbox(conn, limit=min(limit, 50))
    return {"items": items, "count": len(items)}


@router.post("/outbox/{outbox_id}/ack", dependencies=[Depends(_require_bot)])
async def ack(outbox_id: int, body: AckIn):
    """
    Acknowledge a bot command.
    Call with success=true on completion, success=false + error on failure.
    Failed items remain visible for re-queuing or alerting.
    """
    with get_db() as conn:
        ack_outbox(conn, outbox_id, success=body.success, error=body.error)
    return {"ok": True, "outbox_id": outbox_id}


# ── Players ───────────────────────────────────────────────────────────────────

@router.post("/players/{discord_id}/upsert", dependencies=[Depends(_require_bot)])
async def upsert_player_api(discord_id: str, body: PlayerUpsertIn):
    """
    Ensure a player profile exists (idempotent).
    Call when a Discord member joins the server or uses their first command.
    """
    with get_db() as conn:
        player = upsert_player(
            conn, discord_id, body.username, body.cubby_channel
        )
    return player


@router.get("/players/{discord_id}", dependencies=[Depends(_require_bot)])
async def get_player_api(discord_id: str):
    """Get a player profile by Discord ID."""
    with get_db() as conn:
        player = get_player(conn, discord_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.get("/players/{discord_id}/characters", dependencies=[Depends(_require_bot)])
async def player_characters(discord_id: str):
    """All characters owned by a Discord user (all statuses)."""
    with get_db() as conn:
        chars = list_player_characters(conn, discord_id)
    return {"characters": chars, "count": len(chars)}


# ── Staff roles ───────────────────────────────────────────────────────────────

@router.get("/staff/roster", dependencies=[Depends(_require_bot)])
async def staff_roster_api():
    """Every player who currently holds an Enoch staff role — for `/staff list`.
    Mirrors the web Admin → Staff tab so the bot and web always agree."""
    with get_db() as conn:
        players = list_all_players(conn)
    roster = [
        {"discord_id": p.get("discord_id"),
         "username": p.get("username"),
         "role": p.get("staff_role")}
        for p in players
        if p.get("staff_role")
    ]
    return {"staff": roster, "count": len(roster)}


@router.post("/staff/role", dependencies=[Depends(_require_bot)])
async def set_staff_role_api(body: StaffRoleIn):
    """Assign or revoke an Enoch staff role from the bot. The *issuing* user
    must hold `manage_roles` (i.e. be an Admin) — the web layer is the single
    authority, so the bot can't escalate. Writes straight to the same
    player_profiles row the web Admin → Staff tab edits: one source of truth,
    two doors. Pass role=null/'' to revoke."""
    with get_db() as conn:
        # Authorize the issuer against the live matrix (Admin = manage_roles).
        actor_role = get_staff_role(conn, body.actor_discord_id)
        if not staff_role_has_permission(actor_role, "manage_roles"):
            raise HTTPException(
                status_code=403,
                detail="You need the Admin role to assign staff roles.",
            )
        role = (body.role or "").strip().lower() or None
        if role is not None and role not in STAFF_ROLES:
            raise HTTPException(status_code=400, detail=f"Unknown role: {body.role!r}")

        # Ensure the target has a profile row to carry the role, then set it.
        upsert_player(conn, body.target_discord_id,
                      body.target_username or body.target_discord_id)
        set_staff_role(conn, body.target_discord_id, role,
                       actor_id=body.actor_discord_id)
    return {"target_discord_id": body.target_discord_id, "role": role}


# ── Characters ────────────────────────────────────────────────────────────────

@router.post("/characters", dependencies=[Depends(_require_bot)], status_code=201)
async def create_character_api(body: CharacterIn):
    """
    Bot submits a new character on a player's behalf.
    Created with status='pending', is_approved=0.
    Staff reviews and approves through the web interface.
    """
    with get_db() as conn:
        # Ensure the player profile exists before creating the character
        upsert_player(conn, body.discord_id, body.username or body.discord_id)
        char = create_character(
            conn,
            discord_id=body.discord_id,
            name=body.name,
            clan=body.clan,
            predator_type=body.predator_type,
            concept=body.concept,
            sire=body.sire,
            covenant=body.covenant,
            has_ingrained_flaw=body.has_ingrained_flaw,
        )
    log.info("Character created via bot API: %s (%s)", body.name, body.discord_id)
    return char


@router.get("/characters/{character_id}", dependencies=[Depends(_require_bot)])
async def get_character_api(character_id: int):
    """Full character data by ID — for bot embeds and display commands."""
    with get_db() as conn:
        char = get_character(conn, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    return char


@router.patch("/characters/{character_id}/status", dependencies=[Depends(_require_bot)])
async def set_character_status(character_id: int, body: CharacterStatusIn):
    """
    Update a character's lifecycle status (active → retired | dead).
    The bot calls this after in-game events that retire or kill a character.
    Approval changes are handled exclusively through the web staff interface.
    """
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")

        # Bots may not use this endpoint to approve characters
        if body.status not in ("active", "retired", "dead"):
            raise HTTPException(status_code=400, detail="Invalid status")

        updated = update_character(conn, character_id, status=body.status)
        write_audit(
            conn,
            actor_id="bot",
            action=f"set_status_{body.status}",
            target_type="character",
            target_id=character_id,
            before={"status": char["status"]},
            after={"status": body.status, "note": body.note},
        )
    return updated


@router.post("/characters/{character_id}/state", dependencies=[Depends(_require_bot)])
async def apply_state_delta(character_id: int, body: DamageDeltaIn):
    """Apply incremental updates to a character's damage tracks / hunger / humanity.

    Used by the dice bot to push roll outcomes back to the sheet. All values
    are clamped: damage tracks 0..15, hunger 0..5, humanity 0..10. Returns
    the new resolved state so the bot can confirm what landed.
    """
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")

        sheet = dict(char.get("sheet_json") or {})

        def _apply(key: str, delta: int, lo: int, hi: int) -> int:
            if not delta:
                return sheet.get(key, 0)
            new = max(lo, min(hi, (sheet.get(key, 0) or 0) + delta))
            if new == 0:
                sheet.pop(key, None)
            else:
                sheet[key] = new
            return new

        result = {
            "damage_health_sup":    _apply("damage_health_sup",    body.damage_health_sup,    0, 15),
            "damage_health_agg":    _apply("damage_health_agg",    body.damage_health_agg,    0, 15),
            "damage_willpower_sup": _apply("damage_willpower_sup", body.damage_willpower_sup, 0, 15),
            "damage_willpower_agg": _apply("damage_willpower_agg", body.damage_willpower_agg, 0, 15),
            "hunger":               _apply("hunger",               body.hunger,                0, 5),
            "humanity":             _apply("humanity",             body.humanity,              0, 10),
        }

        update_character(conn, character_id, sheet_json=sheet)
        write_audit(
            conn,
            actor_id="bot",
            action="apply_state_delta",
            target_type="character",
            target_id=character_id,
            after={"deltas": body.model_dump(exclude={"source"}), "source": body.source},
        )

    return {"character_id": character_id, "state": result}


@router.post("/characters/{character_id}/macros", dependencies=[Depends(_require_bot)])
async def set_macro(character_id: int, body: MacroIn):
    """Save or delete a named roll macro on a character's sheet — the dice bot
    uses these for `/roll <name>`. Pass an empty expression to delete. Capped
    at 25 macros per character."""
    name = body.name.strip()[:40]
    if not name:
        raise HTTPException(status_code=400, detail="Macro name required")
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        sheet = dict(char.get("sheet_json") or {})
        macros = dict(sheet.get("macros") or {})
        expr = (body.expression or "").strip()
        if expr:
            if name not in macros and len(macros) >= 25:
                raise HTTPException(status_code=400, detail="Macro limit reached (25)")
            macros[name] = expr[:120]
        else:
            macros.pop(name, None)
        if macros:
            sheet["macros"] = macros
        else:
            sheet.pop("macros", None)
        update_character(conn, character_id, sheet_json=sheet)
    return {"character_id": character_id, "macros": macros}


@router.post("/characters/{character_id}/conditions", dependencies=[Depends(_require_bot)])
async def set_condition(character_id: int, body: ConditionIn):
    """Add or clear a transient condition on a character's sheet (the bot's
    `/condition` command). ``active=False`` removes the named condition.
    Matched case-insensitively by name; capped at 25."""
    name = body.name.strip()[:40]
    if not name:
        raise HTTPException(status_code=400, detail="Condition name required")
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        sheet = dict(char.get("sheet_json") or {})
        conditions = [c for c in (sheet.get("conditions") or [])
                      if isinstance(c, dict) and c.get("name")]
        low = name.lower()
        conditions = [c for c in conditions if c["name"].strip().lower() != low]
        if body.active:
            if len(conditions) >= 25:
                raise HTTPException(status_code=400,
                                    detail="Condition limit reached (25)")
            entry = {"name": name}
            if (body.note or "").strip():
                entry["note"] = body.note.strip()[:120]
            conditions.append(entry)
        if conditions:
            sheet["conditions"] = conditions
        else:
            sheet.pop("conditions", None)
        update_character(conn, character_id, sheet_json=sheet)
    return {"character_id": character_id, "conditions": conditions}


@router.post("/characters/{character_id}/bonds", dependencies=[Depends(_require_bot)])
async def set_bond(character_id: int, body: BondIn):
    """Set or adjust a blood bond this character holds toward a regnant (the
    bot's `/bond` command). ``delta`` adjusts relatively (a drink is +1),
    ``level`` sets absolutely; the result is clamped to 0-6 (3 = full bond, 6 =
    max) and a 0 clears the bond. Matched case-insensitively by regnant;
    capped at 25 regnants."""
    regnant = body.regnant.strip()[:60]
    if not regnant:
        raise HTTPException(status_code=400, detail="Regnant name required")
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        sheet = dict(char.get("sheet_json") or {})
        bonds = [b for b in (sheet.get("bonds") or [])
                 if isinstance(b, dict) and b.get("regnant")]
        low = regnant.lower()
        existing = next((b for b in bonds
                         if b["regnant"].strip().lower() == low), None)
        current = int(existing.get("level", 0)) if existing else 0
        if body.delta is not None:
            new_level = current + int(body.delta)
        elif body.level is not None:
            new_level = int(body.level)
        else:
            new_level = current
        new_level = max(0, min(6, new_level))
        bonds = [b for b in bonds if b["regnant"].strip().lower() != low]
        if new_level > 0:
            if len(bonds) >= 25:
                raise HTTPException(status_code=400, detail="Bond limit reached (25)")
            bonds.append({"regnant": regnant, "level": new_level})
        if bonds:
            sheet["bonds"] = bonds
        else:
            sheet.pop("bonds", None)
        update_character(conn, character_id, sheet_json=sheet)
    return {"character_id": character_id, "bonds": bonds}


@router.get("/characters/{character_id}/backgrounds", dependencies=[Depends(_require_bot)])
async def get_backgrounds(character_id: int):
    """List a character's tracked backgrounds (the bot's `/blank` autocomplete +
    status). Includes the active night's label so the bot can show context."""
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        backgrounds = list_character_backgrounds(conn, character_id)
        active = get_active_period(conn)
    return {
        "character_id": character_id,
        "current_night": active["label"] if active else None,
        "backgrounds": backgrounds,
    }


@router.post("/characters/{character_id}/backgrounds/blank", dependencies=[Depends(_require_bot)])
async def blank_background(character_id: int, body: BackgroundBlankIn):
    """Blank N dots of a tracked background for the current night (the bot's
    `/blank` command). Requires an active period and an already-tracked
    background; invalid input returns 400 with the reason."""
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        try:
            result = blank_character_background(
                conn, character_id, body.name, body.dots, updated_by="bot"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"character_id": character_id, "result": result}


@router.get("/characters/{character_id}/projects", dependencies=[Depends(_require_bot)])
async def get_projects(character_id: int):
    """List a character's projects (the bot's `/project list` + `/project roll`).
    Each project carries `can_roll_now` for whether it can be rolled this night."""
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        projects = list_projects_for_character(conn, character_id)
        active   = get_active_period(conn)
        rolls    = timeskip_rolls_remaining(conn, character_id)
    can_roll = active is not None and rolls["remaining"] > 0
    for p in projects:
        p["can_roll_now"] = bool(
            can_roll and p.get("status") == "active"
            and p.get("progress_type") == "roll"
        )
    return {
        "character_id":  character_id,
        "current_night": active["label"] if active else None,
        "rolls":         rolls,
        "projects":      projects,
    }


@router.post("/projects/{project_id}/roll", dependencies=[Depends(_require_bot)])
async def roll_project(project_id: int, body: ProjectRollIn):
    """Accumulate a downtime extended-test roll's successes toward a roll
    project. Validates that the requester owns the project's character and that
    it hasn't already been rolled this period (400 on any invalid state)."""
    with get_db() as conn:
        proj = get_project(conn, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        char = get_character(conn, proj["character_id"])
        if not char or char.get("discord_id") != body.requester_discord_id:
            raise HTTPException(status_code=403, detail="That isn't your project.")
        active    = get_active_period(conn)
        period_id = active["id"] if active else None
        try:
            if proj.get("stages_json"):
                res = resolve_project_roll(
                    conn, project_id, successes=body.successes,
                    critical=body.critical, messy=body.messy,
                    hunger_one=body.hunger_one, pool_size=body.pool_size,
                    period_id=period_id,
                )
                project, result = res["project"], res["result"]
            else:
                project = record_project_roll(
                    conn, project_id, successes=body.successes,
                    outcome=body.outcome, period_id=period_id,
                )
                result = {"outcome": body.outcome or "progress"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"project": project, "result": result}


@router.get("/sites", dependencies=[Depends(_require_bot)])
async def bot_list_sites():
    """Active hunting sites — for the bot's `/hunt` site picker. Each site
    carries its base ``predator_dcs`` (keyed by predator-type name), the
    Chasse-reduced ``effective_dcs``, the controlling ``coterie_id``, and
    ``blood_quality`` so the bot can resolve a feeding roll's difficulty and
    how much Hunger a feed slakes."""
    with get_db() as conn:
        sites = list_hunting_sites(conn, active_only=True)
    return {
        "sites": [
            {
                "id":            s["id"],
                "name":          s["name"],
                "borough":       s.get("borough"),
                "blood_quality": s.get("blood_quality", 1),
                "predator_dcs":  s.get("predator_dcs") or {},
                "effective_dcs": s.get("effective_dcs") or {},
                "coterie_id":    s.get("coterie_id"),
                "chasse_reduction": s.get("chasse_reduction", 0),
                "controlling_coterie": s.get("controlling_coterie"),
            }
            for s in sites
        ]
    }


class HuntLogIn(BaseModel):
    character_id: int
    outcome:      str = Field(..., description="clean | success | messy_critical | bestial_failure")
    note:         str = ""


@router.post("/sites/{site_id}/hunt", dependencies=[Depends(_require_bot)],
             status_code=201)
async def bot_log_hunt(site_id: int, body: HuntLogIn):
    """Record a hunt outcome for a character at a site. Called by the
    dice bot after a feeding roll completes so the chronicle's site
    activity feed reflects what actually happened on the dice."""
    if body.outcome not in HUNT_OUTCOMES:
        raise HTTPException(status_code=400,
                            detail=f"outcome must be one of {HUNT_OUTCOMES}")
    with get_db() as conn:
        site = get_hunting_site(conn, site_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")
        char = get_character(conn, body.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        row = create_hunt_log(
            conn,
            site_id=site_id,
            character_id=body.character_id,
            outcome=body.outcome,
            note=body.note,
            source="bot",
        )
    return {"hunt_id": row["id"], "outcome": row["outcome"],
            "hunted_at": row["hunted_at"]}


@router.get("/characters/{character_id}/coterie", dependencies=[Depends(_require_bot)])
async def character_coterie(character_id: int):
    """Coterie info for a character — for `/coterie status` bot command.

    Returns the coterie's domain stats (chasse / lien / portillon) plus
    its current members. Returns 404 if the character has no coterie.
    """
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        coterie = get_coterie_for_character(conn, character_id)
        if not coterie:
            raise HTTPException(status_code=404, detail="Character is not in a coterie")
        members = list_coterie_members(conn, coterie["id"])

    return {
        "character_id":   character_id,
        "character_name": char["name"],
        "coterie": {
            "id":           coterie["id"],
            "name":         coterie["name"],
            "chasse":       coterie["chasse"],
            "lien":         coterie["lien"],
            "portillon":    coterie["portillon"],
            "status":       coterie["status"],
            "member_count": len(members),
        },
        "members": [
            {
                "character_id": m["character_id"],
                "name":         m["character_name"],
                "clan":         m["character_clan"],
                "role":         m["role"],
                "player":       m.get("player_username"),
            }
            for m in members
        ],
    }


@router.get("/characters/{character_id}/history", dependencies=[Depends(_require_bot)])
async def character_history(character_id: int):
    """XP claims and spend requests for a character — for `/xp` bot command."""
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        claims = list_claims_for_character(conn, character_id)
        spends = list_spends_for_character(conn, character_id)

    return {
        "character_id": character_id,
        "xp_total":     char["xp_total"],
        "xp_spent":     char["xp_spent"],
        "xp_available": char["xp_available"],
        "xp_cap":       char["xp_cap"],
        "claims":       claims[:20],
        "spends":       spends[:20],
    }


# ── Roster ────────────────────────────────────────────────────────────────────

@router.get("/roster", dependencies=[Depends(_require_bot)])
async def roster(clan: str | None = None):
    """
    All approved active characters — for roster channel posts.
    Optionally filter by clan slug (e.g. ?clan=tremere).
    """
    with get_db() as conn:
        chars = list_characters(conn, status="active", clan=clan)
    approved = [c for c in chars if c["is_approved"]]
    return {
        "characters": approved,
        "count":      len(approved),
        "clan_filter": clan,
    }


# ── Period ────────────────────────────────────────────────────────────────────

@router.get("/period/active", dependencies=[Depends(_require_bot)])
async def active_period():
    """
    Current open play period (the chronicle's "timeskip") — the bot checks
    this before allowing /claim commands and renders it for /timeskip.
    Returns { active: false, period: null } when no window is open, plus the
    next few `upcoming` periods so the bot can show what's on deck.
    """
    with get_db() as conn:
        period = get_active_period(conn)
        upcoming = list_upcoming_periods(conn, limit=3)
    return {"active": period is not None, "period": period,
            "upcoming": [dict(p) for p in upcoming]}
