"""api.py — Thin async wrapper around the Enoch web API."""
import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.BOT_SERVICE_TOKEN}"}


def _base() -> str:
    return settings.WEB_URL.rstrip("/")


async def report_alert(level: str, event: str, message: str, detail: str = "") -> None:
    """Surface a bot-side warn/error on the web staff alerts page. Best-effort —
    swallows all failures (it's called from error handlers and must never raise)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.post(
                f"{_base()}/api/alerts",
                json={"level": level, "event": event[:80],
                      "message": message[:500], "detail": (detail or "")[:8000]},
                headers=_headers(),
            )
    except Exception:
        log.debug("report_alert failed", exc_info=True)


# ── Players ───────────────────────────────────────────────────────────────────

async def upsert_player(discord_id: str, username: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/players/{discord_id}/upsert",
            json={"username": username},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def get_player_characters(discord_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/players/{discord_id}/characters",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()["characters"]


# ── Staff roles ───────────────────────────────────────────────────────────────

async def set_staff_role(actor_discord_id: str, target_discord_id: str,
                         target_username: str, role: str | None) -> dict:
    """Assign (or revoke, with ``role=None``) an Enoch staff role from the bot.
    The issuing user must be an Admin — the web enforces it. Returns
    ``{target_discord_id, role}`` on success, or ``{error: <reason>}`` when the
    issuer lacks permission or the role is invalid, so the caller can surface it."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/staff/role",
            json={"actor_discord_id": actor_discord_id,
                  "target_discord_id": target_discord_id,
                  "target_username": target_username,
                  "role": role},
            headers=_headers(),
        )
        if r.status_code in (400, 403):
            try:
                return {"error": r.json().get("detail") or "Could not set the role."}
            except Exception:
                return {"error": "Could not set the role."}
        r.raise_for_status()
        return r.json()


async def get_staff_roster() -> list[dict]:
    """Every player holding an Enoch staff role, for `/staff list`. Each entry
    is ``{discord_id, username, role}``."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_base()}/api/staff/roster", headers=_headers())
        r.raise_for_status()
        return r.json()["staff"]


# ── Chronicle settings ────────────────────────────────────────────────────────

async def get_chronicle_settings(actor: str | None = None) -> dict:
    """Curated chronicle settings for the `/settings` menu. Pass ``actor`` (the
    invoking user's discord id) to learn whether they're allowed to edit
    (``editable`` in the response)."""
    params = {"actor": actor} if actor else None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_base()}/api/settings", headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()


async def update_chronicle_settings(actor_discord_id: str, fields: dict) -> dict:
    """Update curated chronicle settings from the bot. The issuing user must be a
    Settings Admin — the web enforces it. Returns the updated settings, or
    ``{error: <reason>}`` when the issuer lacks permission or a value is invalid."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/settings",
            json={"actor_discord_id": actor_discord_id, "fields": fields},
            headers=_headers(),
        )
        if r.status_code in (400, 403):
            try:
                return {"error": r.json().get("detail") or "Could not update settings."}
            except Exception:
                return {"error": "Could not update settings."}
        r.raise_for_status()
        return r.json()


# ── Characters ────────────────────────────────────────────────────────────────

async def create_character(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters",
            json=data,
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def get_character(character_id: int) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/characters/{character_id}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def log_roll(character_id: int, *, kind: str = "roll", pool: int = 0,
                   hunger: int = 0, difficulty: int = 0, successes: int = 0,
                   outcome: str = "", label: str | None = None,
                   dice: str | None = None) -> None:
    """Record a roll in the shared web history (best-effort; never raises so it
    can't break a roll)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.post(
                f"{_base()}/api/characters/{character_id}/rolls",
                json={"kind": kind, "pool": pool, "hunger": hunger,
                      "difficulty": difficulty, "successes": successes,
                      "outcome": outcome, "label": label, "dice": dice},
                headers=_headers(),
            )
    except Exception:
        log.debug("log_roll failed", exc_info=True)


async def recent_rolls(character_id: int, limit: int = 5) -> list[dict]:
    """A character's most-recent rolls (newest first) for `/character sheet`."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/characters/{character_id}/rolls",
            params={"limit": limit},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()["rolls"]


async def apply_state_delta(character_id: int, *, hunger: int = 0,
                            damage_health_sup: int = 0,
                            damage_willpower_sup: int = 0, humanity: int = 0,
                            source: str | None = None) -> dict:
    """Push a delta to a character's tracked state (Hunger / Health /
    Willpower / Humanity) back to the sheet. Returns ``{character_id, state}``
    with the new clamped values. Used by the dice roller for Rouse / wake /
    mend / remorse."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters/{character_id}/state",
            json={"hunger": hunger,
                  "damage_health_sup": damage_health_sup,
                  "damage_willpower_sup": damage_willpower_sup,
                  "humanity": humanity,
                  "source": source},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def set_macro(character_id: int, name: str,
                    expression: str | None) -> dict:
    """Save (or delete, with an empty expression) a named roll macro on a
    character's sheet. Returns ``{character_id, macros}``."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters/{character_id}/macros",
            json={"name": name, "expression": expression},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def set_condition(character_id: int, name: str, *, note: str | None = None,
                        active: bool = True) -> dict:
    """Add (``active=True``) or clear (``active=False``) a transient condition
    on a character's sheet. Returns ``{character_id, conditions}``."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters/{character_id}/conditions",
            json={"name": name, "note": note, "active": active},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def set_bond(character_id: int, regnant: str, *, level: int | None = None,
                   delta: int | None = None) -> dict:
    """Set (``level``) or adjust (``delta``, +1 per drink) a blood bond toward
    a regnant. The result is clamped 0-3; 0 clears it. Returns
    ``{character_id, bonds}``."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters/{character_id}/bonds",
            json={"regnant": regnant, "level": level, "delta": delta},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def get_backgrounds(character_id: int) -> dict:
    """List a character's tracked backgrounds (for /blank autocomplete + status).
    Returns ``{character_id, current_night, backgrounds}``."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/characters/{character_id}/backgrounds",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def blank_background(character_id: int, name: str, dots: int = 1) -> dict:
    """Blank ``dots`` of a tracked background for the current night. Returns
    ``{character_id, result}`` on success, or ``{error: <reason>}`` when the
    request is rejected (no active night, background not tracked, too many
    dots) so the caller can surface the message."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters/{character_id}/backgrounds/blank",
            json={"name": name, "dots": dots},
            headers=_headers(),
        )
        if r.status_code == 400:
            try:
                return {"error": r.json().get("detail") or "Invalid request."}
            except Exception:
                return {"error": "Invalid request."}
        r.raise_for_status()
        return r.json()


async def get_projects(character_id: int) -> dict:
    """List a character's downtime projects (for /project list + /project roll).
    Returns ``{character_id, current_night, projects}``; each project has a
    ``can_roll_now`` flag."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/characters/{character_id}/projects",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def record_project_roll(project_id: int, requester_discord_id: str,
                              successes: int, outcome: str, *,
                              critical: bool = False, messy: bool = False,
                              hunger_one: bool = False, pool_size: int = 0) -> dict:
    """Post a downtime roll for a roll project; the web resolves it against the
    project's current stage. Returns ``{project, result}`` on success or
    ``{error: <reason>}`` when rejected (not your project, no rolls left this
    timeskip, no active period, not a roll project)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/projects/{project_id}/roll",
            json={"requester_discord_id": requester_discord_id,
                  "successes": successes, "outcome": outcome,
                  "critical": critical, "messy": messy,
                  "hunger_one": hunger_one, "pool_size": pool_size},
            headers=_headers(),
        )
        if r.status_code in (400, 403, 404):
            try:
                return {"error": r.json().get("detail") or "Could not record the roll."}
            except Exception:
                return {"error": "Could not record the roll."}
        r.raise_for_status()
        return r.json()


async def get_character_coterie(character_id: int) -> dict | None:
    """Fetch coterie info for a character. Returns None if not in a coterie."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/characters/{character_id}/coterie",
            headers=_headers(),
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


# ── Hunting ─────────────────────────────────────────────────────────────────--

async def list_hunting_sites() -> list[dict]:
    """All active hunting sites with their DCs, blood quality, and controlling
    coterie — for the `/hunt` site picker."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_base()}/api/sites", headers=_headers())
        r.raise_for_status()
        return r.json()["sites"]


async def log_hunt(site_id: int, character_id: int, outcome: str,
                   note: str = "") -> dict:
    """Record a feeding outcome at a site in the chronicle's activity feed.
    ``outcome`` is one of clean | success | messy_critical | bestial_failure."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/sites/{site_id}/hunt",
            json={"character_id": character_id, "outcome": outcome, "note": note},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


# ── Outbox ────────────────────────────────────────────────────────────────────

async def drain_outbox(limit: int = 20) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/outbox",
            params={"limit": limit},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()["items"]


async def ack_outbox(
    outbox_id: int,
    success: bool = True,
    error: str | None = None,
) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/outbox/{outbox_id}/ack",
            json={"success": success, "error": error},
            headers=_headers(),
        )
        r.raise_for_status()


# ── Period ────────────────────────────────────────────────────────────────────

async def get_active_period() -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/api/period/active",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
