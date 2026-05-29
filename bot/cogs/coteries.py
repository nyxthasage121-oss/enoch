"""coteries.py — /coterie slash commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_character_coterie, get_player_characters
from ..config import settings

log = logging.getLogger(__name__)

_GOLD  = 0xC8A85B
_BLOOD = 0x8B1A1A
_MAUVE = 0x7e4ac9


def _web(path: str) -> str:
    return settings.WEB_URL.rstrip("/") + path


def _dots(rating: int, max_dots: int = 5) -> str:
    rating = max(0, min(max_dots, rating or 0))
    return "●" * rating + "○" * (max_dots - rating)


class CoteriesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    coterie = app_commands.Group(name="coterie", description="Coterie information")

    @coterie.command(
        name="status",
        description="Show your coterie's members and domain stats.",
    )
    @app_commands.describe(name="Character name (only required if you have more than one)")
    async def coterie_status(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        try:
            characters = await get_player_characters(discord_id)
        except Exception as exc:
            log.warning("coterie status: get_player_characters failed for %s: %s", discord_id, exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True,
            )
            return

        active = [c for c in characters if c["is_approved"] and c["status"] == "active"]
        if not active:
            await interaction.followup.send(
                "You have no active approved characters. Use `/character submit` to create one.",
                ephemeral=True,
            )
            return

        # Pick the right character
        if name:
            target = next(
                (c for c in active if c["name"].lower() == name.strip().lower()),
                None,
            )
            if not target:
                await interaction.followup.send(
                    f"No active character named **{name}** found. "
                    f"Use `/character list` to see your characters.",
                    ephemeral=True,
                )
                return
        elif len(active) == 1:
            target = active[0]
        else:
            names = ", ".join(f"`{c['name']}`" for c in active)
            await interaction.followup.send(
                f"You have multiple characters — please specify which one. "
                f"Try `/coterie status name:<...>`.\n\nOptions: {names}",
                ephemeral=True,
            )
            return

        # Fetch coterie info
        try:
            data = await get_character_coterie(target["id"])
        except Exception as exc:
            log.warning("coterie status: API call failed for %d: %s", target["id"], exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True,
            )
            return

        if data is None:
            await interaction.followup.send(
                f"**{target['name']}** is not in a coterie.\n\n"
                "Coterie formations are reviewed by staff — submit a proposal on **Enoch**.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=_build_coterie_embed(data), ephemeral=True)


def _build_coterie_embed(data: dict) -> discord.Embed:
    """Render a coterie status payload as a Discord embed."""
    co       = data["coterie"]
    members  = data["members"]

    e = discord.Embed(
        title=f"🩸 {co['name']}",
        description=f"_Coterie of **{data['character_name']}** and {len(members) - 1} other{'s' if len(members) != 2 else ''}._",
        color=_GOLD if co["status"] == "active" else _MAUVE,
    )

    # Domain stats — chasse / lien / portillon
    domain = (
        f"Chasse     {_dots(co['chasse'])}\n"
        f"Lien       {_dots(co['lien'])}\n"
        f"Portillon  {_dots(co['portillon'])}"
    )
    e.add_field(name="Domain", value=f"```\n{domain}\n```", inline=False)

    # Members list
    member_lines = []
    for m in members:
        clan = (m.get("clan") or "").replace("-", " ").title()
        line = f"• **{m['name']}**"
        if clan:
            line += f"  ·  _{clan}_"
        if m.get("role") == "leader":
            line += "  ·  👑 Leader"
        if m.get("player"):
            line += f"  ·  `{m['player']}`"
        member_lines.append(line)
    e.add_field(name=f"Members ({len(members)})", value="\n".join(member_lines), inline=False)

    e.set_footer(text=f"Status: {co['status'].title()}  ·  Manage on Enoch")
    return e


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CoteriesCog(bot))
