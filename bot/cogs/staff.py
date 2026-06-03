"""staff.py — `/staff` role management from Discord.

Assign or revoke Enoch staff roles without leaving Discord. A change writes to
the same player_profiles row the web Admin → Staff tab edits, so the two stay
in lockstep — one source of truth, two doors. Only Admins can assign roles; the
web API enforces it (the bot can't escalate its own authority).

This sets the *Enoch* role (which grants XP powers), not the Discord role —
you keep managing those in Discord.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_staff_roster, set_staff_role

log = logging.getLogger(__name__)

_GOLD = 0xC8A85B

# Assignable roles, mirrored from web/db.py::STAFF_ROLES. Explicit Choices so
# Discord renders a clean dropdown (highest authority first).
_ROLE_CHOICES = [
    app_commands.Choice(name="Admin", value="admin"),
    app_commands.Choice(name="Moderator", value="moderator"),
    app_commands.Choice(name="Storyteller", value="storyteller"),
    app_commands.Choice(name="Helper", value="helper"),
]

# Display order for the roster, highest authority first.
_ROLE_ORDER = ["admin", "moderator", "storyteller", "helper"]


def build_roster_embed(roster: list[dict]) -> discord.Embed:
    """Render the current staff roster grouped by role (offline-testable)."""
    e = discord.Embed(title="🗝️ Enoch Staff", color=_GOLD)
    if not roster:
        e.description = "No staff roles assigned yet."
        return e
    by_role: dict[str, list[str]] = {}
    for m in roster:
        by_role.setdefault(m.get("role"), []).append(
            m.get("username") or str(m.get("discord_id")))
    for role in _ROLE_ORDER:
        names = by_role.get(role)
        if names:
            e.add_field(name=role.title(),
                        value="\n".join(f"• {n}" for n in names), inline=False)
    return e


class StaffCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    staff = app_commands.Group(
        name="staff",
        description="Manage Enoch staff roles (Admins only)",
        guild_only=True,
    )

    @staff.command(name="role", description="Assign an Enoch staff role to a member.")
    @app_commands.describe(member="Who to assign", role="Which role to give them")
    @app_commands.choices(role=_ROLE_CHOICES)
    async def staff_role(self, interaction: discord.Interaction,
                         member: discord.Member,
                         role: app_commands.Choice[str]) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await set_staff_role(
            actor_discord_id=str(interaction.user.id),
            target_discord_id=str(member.id),
            target_username=member.display_name,
            role=role.value,
        )
        if result.get("error"):
            await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ {member.mention} is now **{role.name}**.", ephemeral=True)

    @staff.command(name="revoke", description="Remove a member's Enoch staff role.")
    @app_commands.describe(member="Whose staff role to clear")
    async def staff_revoke(self, interaction: discord.Interaction,
                           member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await set_staff_role(
            actor_discord_id=str(interaction.user.id),
            target_discord_id=str(member.id),
            target_username=member.display_name,
            role=None,
        )
        if result.get("error"):
            await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Cleared {member.mention}'s staff role.", ephemeral=True)

    @staff.command(name="list", description="Show who currently holds an Enoch staff role.")
    async def staff_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            roster = await get_staff_roster()
        except Exception as exc:
            log.warning("staff list: roster fetch failed: %s", exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True)
            return
        await interaction.followup.send(embed=build_roster_embed(roster), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
