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
