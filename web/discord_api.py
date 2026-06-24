"""discord_api.py — thin, read-only Discord REST helpers for the admin UI.

Uses the bot token to list the guild's text channels and roles so staff can
pick from dropdowns instead of pasting raw snowflakes. Best-effort by design:
every public helper returns ``[]`` when the bot token or guild id is unset, or
on any API/timeout error — callers (the admin template) fall back to a manual
ID field, so the app works the same with or without the token configured.

Results are cached in-process for a few minutes to avoid hammering Discord's
rate limits on every admin render.
"""
import time

import httpx

from .config import settings

# path -> (expires_at_epoch, parsed_json)
_CACHE: dict[str, tuple[float, object]] = {}
_TTL = 300.0          # 5 minutes
_TIMEOUT = 4.0        # seconds; a slow Discord must not hang the admin page

# Discord channel types that can receive a normal message.
_TEXTLIKE = (0, 5)    # 0 = GUILD_TEXT, 5 = GUILD_ANNOUNCEMENT


async def _guild_json(path: str):
    """GET /guilds/{id}{path} with the bot token. Returns parsed JSON, or None
    when unavailable (no token/guild, network error, non-2xx). Cached per path."""
    token = settings.DISCORD_BOT_TOKEN
    guild = settings.DISCORD_GUILD_ID
    if not token or not guild:
        return None
    now = time.time()
    hit = _CACHE.get(path)
    if hit and hit[0] > now:
        return hit[1]
    url = f"https://discord.com/api/v10/guilds/{guild}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers={"Authorization": f"Bot {token}"})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    _CACHE[path] = (now + _TTL, data)
    return data


def _parse_channels(data) -> list[dict]:
    """Text-capable channels as [{id, name, category}], sorted for display."""
    if not data:
        return []
    cats = {c["id"]: c.get("name", "") for c in data if c.get("type") == 4}
    chans = [c for c in data if c.get("type") in _TEXTLIKE]
    chans.sort(key=lambda c: (cats.get(c.get("parent_id"), ""),
                              c.get("position", 0), c.get("name", "")))
    return [{"id": str(c["id"]), "name": c.get("name", "?"),
             "category": cats.get(c.get("parent_id"), "")} for c in chans]


def _parse_roles(data, guild_id) -> list[dict]:
    """Assignable roles as [{id, name}] — excludes @everyone and managed
    (bot/integration) roles, highest first."""
    if not data:
        return []
    gid = str(guild_id)
    roles = [r for r in data
             if str(r.get("id")) != gid and not r.get("managed")]
    roles.sort(key=lambda r: (-(r.get("position", 0)), r.get("name", "")))
    return [{"id": str(r["id"]), "name": r.get("name", "?")} for r in roles]


async def guild_text_channels() -> list[dict]:
    """The guild's text-capable channels, or [] when the bot token/guild is
    unset or Discord is unreachable (the UI then shows a manual ID field)."""
    return _parse_channels(await _guild_json("/channels"))


async def guild_roles() -> list[dict]:
    """The guild's assignable roles, or [] when unavailable."""
    return _parse_roles(await _guild_json("/roles"), settings.DISCORD_GUILD_ID)
