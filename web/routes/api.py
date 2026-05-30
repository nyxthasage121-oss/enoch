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
    create_character,
    create_hunt_log,
    drain_outbox,
    get_active_period,
    get_character,
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
    """A blood bond this character holds toward a ``regnant`` (1-3 dots; 3 is
    a full bond). Pass ``delta`` for a relative change (e.g. +1 per drink) or
    ``level`` to set it absolutely. A resulting level of 0 clears the bond."""
    regnant: str = Field(..., min_length=1, max_length=60)
    level:   int | None = None
    delta:   int | None = None


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
    ``level`` sets absolutely; the result is clamped to 0-3 and a 0 clears the
    bond. Matched case-insensitively by regnant; capped at 25."""
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
        new_level = max(0, min(3, new_level))
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
