"""timeskip.py — `/timeskip` shows the chronicle's current play period.

A "timeskip" is the chronicle's open play window (a row in ``play_periods``):
when downtime opens and closes. This command is read-only — it surfaces the
active period plus the next few on deck. Staff open/close periods on the web.
"""
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_active_period

log = logging.getLogger(__name__)

_GOLD = 0xC8A85B
_INK  = 0x6b5a8e


def _epoch(iso: str | None) -> int | None:
    """Parse an ISO-8601 timestamp (UTC assumed if naïve) to a Unix epoch."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _ts(iso: str | None, style: str = "F") -> str:
    """A Discord timestamp tag (renders localized + relative per viewer)."""
    e = _epoch(iso)
    return f"<t:{e}:{style}>" if e is not None else "—"


def _kind(period: dict) -> str:
    """Human label for a period's type · phase, skipping empty/'full' noise."""
    bits = []
    pt = (period.get("period_type") or "").strip()
    ph = (period.get("phase") or "").strip()
    if pt:
        bits.append(pt.replace("_", " ").title())
    if ph and ph.lower() not in ("full", "none"):
        bits.append(ph.replace("_", " ").title())
    return " · ".join(bits)


def build_timeskip_embed(data: dict) -> discord.Embed:
    """Render the active period + upcoming ones (offline-testable)."""
    period = (data or {}).get("period") if (data or {}).get("active") else None
    upcoming = [p for p in ((data or {}).get("upcoming") or [])
                if isinstance(p, dict) and p.get("label")]

    if period:
        e = discord.Embed(
            title="🌙 Current Timeskip",
            description=f"**{period.get('label', 'Untitled Period')}**",
            color=_GOLD)
        kind = _kind(period)
        if kind:
            e.add_field(name="Kind", value=kind, inline=False)
        e.add_field(
            name="Window",
            value=(f"Opened {_ts(period.get('opens_at'))} "
                   f"({_ts(period.get('opens_at'), 'R')})\n"
                   f"Closes {_ts(period.get('closes_at'))} "
                   f"({_ts(period.get('closes_at'), 'R')})"),
            inline=False)
    else:
        e = discord.Embed(
            title="🌙 Timeskip",
            description="No timeskip is open right now.",
            color=_INK)

    if upcoming:
        lines = []
        for p in upcoming[:3]:
            kind = _kind(p)
            suffix = f" · {kind}" if kind else ""
            lines.append(f"• **{p['label']}** — opens {_ts(p.get('opens_at'), 'R')}{suffix}")
        e.add_field(name="On Deck", value="\n".join(lines), inline=False)
    elif not period:
        e.add_field(name="On Deck", value="_Nothing scheduled yet._", inline=False)

    return e


class TimeskipCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="timeskip",
        description="Show the chronicle's current timeskip (play period) and what's next.")
    async def timeskip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            data = await get_active_period()
        except Exception as exc:
            log.warning("timeskip: get_active_period failed: %s", exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True)
            return
        await interaction.followup.send(embed=build_timeskip_embed(data),
                                        ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimeskipCog(bot))
