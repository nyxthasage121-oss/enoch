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


async def apply_state_delta(character_id: int, *, hunger: int = 0,
                            damage_health_sup: int = 0,
                            damage_willpower_sup: int = 0,
                            source: str | None = None) -> dict:
    """Push a delta to a character's tracked state (Hunger / Health /
    Willpower) back to the sheet. Returns ``{character_id, state}`` with the
    new clamped values. Used by the dice roller for Rouse/wake/mend."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_base()}/api/characters/{character_id}/state",
            json={"hunger": hunger,
                  "damage_health_sup": damage_health_sup,
                  "damage_willpower_sup": damage_willpower_sup,
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
