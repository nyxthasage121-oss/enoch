"""xp.py — /xp slash commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_active_period, get_player_characters
from ..config import settings

log = logging.getLogger(__name__)

_GOLD  = 0xC8A85B
_BLOOD = 0x8B1A1A


def _web(path: str) -> str:
    return settings.WEB_URL.rstrip("/") + path


class XPCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    xp = app_commands.Group(name="xp", description="XP balance and history")

    @xp.command(
        name="submit",
        description="Submit an XP claim for a scene or event.",
    )
    async def xp_submit(self, interaction: discord.Interaction) -> None:
        """Check for an active period, then send a link to the XP claim form."""
        await interaction.response.defer(ephemeral=True)

        period = None
        try:
            resp = await get_active_period()
            if resp and resp.get("active"):
                period = resp.get("period")
        except Exception:
            pass

        if not period:
            await interaction.followup.send(
                "❌ There is no active XP period right now. "
                "Check back when staff opens the next period.",
                ephemeral=True,
            )
            return

        e = discord.Embed(
            title="📋 Submit an XP Claim",
            description=(
                f"**Period:** {period.get('label', 'Current Period')}\n\n"
                "Log in to **Enoch** and fill out the XP claim form. "
                "Staff will review your submission and notify you here once it's processed."
            ),
            color=_GOLD,
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open XP Claim Form",
            style=discord.ButtonStyle.link,
            url=_web("/xp/claim"),
            emoji="🩸",
        ))

        await interaction.followup.send(embed=e, view=view, ephemeral=True)

    @xp.command(
        name="spend",
        description="Request to spend XP on a trait or discipline.",
    )
    async def xp_spend(self, interaction: discord.Interaction) -> None:
        """Send a link to the XP spend form on the web app."""
        e = discord.Embed(
            title="🩸 Spend XP",
            description=(
                "Submit a spend request on **Enoch**. "
                "A Storyteller will review your request and notify you here once it's approved or returned."
            ),
            color=_GOLD,
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open XP Spend Form",
            style=discord.ButtonStyle.link,
            url=_web("/xp/spend"),
            emoji="🩸",
        ))

        await interaction.response.send_message(embed=e, view=view, ephemeral=True)

    @xp.command(name="check", description="Check your XP balance for your active character(s).")
    async def xp_check(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)

        try:
            characters = await get_player_characters(discord_id)
        except Exception as exc:
            log.warning("xp check failed for %s: %s", discord_id, exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True,
            )
            return

        active = [
            c for c in characters
            if c["status"] == "active" and c["is_approved"]
        ]

        if not active:
            # Check if they have pending/unapproved characters
            pending = [c for c in characters if not c["is_approved"]]
            if pending:
                await interaction.followup.send(
                    f"You have **{len(pending)}** character(s) pending staff approval. "
                    "You'll be notified once they're reviewed.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "You have no active characters on record. "
                    "Use `/character submit` to submit one for approval.",
                    ephemeral=True,
                )
            return

        embeds: list[discord.Embed] = []
        for char in active[:10]:   # Discord hard cap: 10 embeds per message
            avail = char["xp_available"]
            total = char["xp_total"]
            spent = char["xp_spent"]
            cap   = char["xp_cap"] or 350
            pct   = round(total / cap * 100) if cap else 0

            color = _BLOOD if total >= cap else _GOLD

            e = discord.Embed(title=f"🩸 {char['name']}", color=color)
            e.add_field(name="Available", value=f"**{avail} XP**", inline=True)
            e.add_field(name="Earned",    value=f"{total} / {cap} ({pct}%)", inline=True)
            e.add_field(name="Spent",     value=str(spent), inline=True)

            if total >= cap:
                e.set_footer(text="⚠️ XP cap reached — speak with staff about retirement.")
            elif char.get("clan"):
                e.set_footer(text=char["clan"].replace("-", " ").title())

            embeds.append(e)

        await interaction.followup.send(embeds=embeds, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XPCog(bot))
