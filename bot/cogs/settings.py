"""settings.py — `/settings` chronicle configuration from Discord.

A thin door onto the web's chronicle settings: read with `/settings show`, flip
the common knobs (post channels, the dice roller, resonance/project mode, the XP
cap) without leaving Discord. Every write goes through the web API, which enforces
the Settings-Admin gate — the bot can't escalate its own authority. Complex config
(tier budgets, chargen, predator unlocks, staff roles) stays on the web.

Setting a channel from Discord is the win here: `/settings dice-channel` in a
channel points posting there with no snowflake-copying.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_chronicle_settings, update_chronicle_settings

log = logging.getLogger(__name__)

_GOLD = 0xC8A85B

# Mirror web/settings_enums.py — kept short here since the bot can't import the
# web package. The web validates the value regardless, so a drift just gets a
# polite error rather than a bad write.
_RESONANCE_CHOICES = [
    app_commands.Choice(name="Standard — V5 core", value="standard"),
    app_commands.Choice(name="Tattered Facade — alt Disciplines", value="tattered_facade"),
    app_commands.Choice(name="Add Empty — +1-in-6 Empty", value="add_empty"),
]
_PROJECT_CHOICES = [
    app_commands.Choice(name="NYbN — multi-stage extended test", value="nybn"),
    app_commands.Choice(name="Homebrew — staff-set goal", value="homebrew"),
    app_commands.Choice(name="Off — Projects disabled", value="off"),
]


def _chan(v) -> str:
    v = str(v or "").strip()
    return f"<#{v}>" if v.isdigit() else "—"


def build_settings_embed(s: dict) -> discord.Embed:
    """Render the curated chronicle settings (offline-testable)."""
    e = discord.Embed(title="⚙️ Chronicle Settings", color=_GOLD)
    e.add_field(name="Dice post channel", value=_chan(s.get("dice_channel_id")), inline=True)
    e.add_field(name="ST tracker channel", value=_chan(s.get("st_channel_id")), inline=True)
    e.add_field(name="Announce channel", value=_chan(s.get("announce_channel_id")), inline=True)
    e.add_field(name="Web dice roller",
                value="✅ on" if s.get("dice_roller_enabled") else "⛔ off", inline=True)
    e.add_field(name="Resonance table", value=str(s.get("resonance_mode") or "standard"), inline=True)
    e.add_field(name="Project mode", value=str(s.get("project_mode") or "nybn"), inline=True)
    e.add_field(name="XP cap",
                value=(f"✅ {s.get('xp_cap_amount', 350)}" if s.get("xp_cap_enabled") else "⛔ off"),
                inline=True)
    e.add_field(name="Chars / player", value=str(s.get("max_chars_per_player", 2)), inline=True)
    e.set_footer(text="Complex config (budgets, chargen, staff roles) lives on the web.")
    return e


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    settings = app_commands.Group(
        name="settings",
        description="View + change chronicle settings (Settings-Admin to change)",
        guild_only=True,
    )

    async def _apply(self, interaction: discord.Interaction, fields: dict, label: str) -> None:
        """Push a settings change through the web API, then confirm or surface the
        error. The caller has already deferred."""
        result = await update_chronicle_settings(str(interaction.user.id), fields)
        if result.get("error"):
            await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ {label}", embed=build_settings_embed(result),
                                        ephemeral=True)

    @settings.command(name="show", description="Show the current chronicle settings.")
    async def settings_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            s = await get_chronicle_settings()
        except Exception as exc:
            log.warning("settings show: fetch failed: %s", exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True)
            return
        await interaction.followup.send(embed=build_settings_embed(s), ephemeral=True)

    async def _set_channel(self, interaction: discord.Interaction, field: str,
                           channel, disable: bool, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if disable:
            await self._apply(interaction, {field: ""}, f"{name} disabled.")
            return
        ch = channel or interaction.channel
        await self._apply(interaction, {field: str(ch.id)}, f"{name} set to {ch.mention}.")

    @settings.command(name="dice-channel",
                      description="Where web rolls post (defaults to this channel).")
    @app_commands.describe(channel="Channel (default: here)", disable="Turn dice posting off")
    async def dice_channel(self, interaction: discord.Interaction,
                           channel: discord.TextChannel | None = None,
                           disable: bool = False) -> None:
        await self._set_channel(interaction, "dice_channel_id", channel, disable, "Dice post channel")

    @settings.command(name="st-channel",
                      description="Where the ST vitals board posts (defaults to this channel).")
    @app_commands.describe(channel="Channel (default: here)", disable="Turn ST posting off")
    async def st_channel(self, interaction: discord.Interaction,
                         channel: discord.TextChannel | None = None,
                         disable: bool = False) -> None:
        await self._set_channel(interaction, "st_channel_id", channel, disable, "ST tracker channel")

    @settings.command(name="announce-channel",
                      description="Where period reminders post (defaults to this channel).")
    @app_commands.describe(channel="Channel (default: here)", disable="Turn announcements off")
    async def announce_channel(self, interaction: discord.Interaction,
                               channel: discord.TextChannel | None = None,
                               disable: bool = False) -> None:
        await self._set_channel(interaction, "announce_channel_id", channel, disable,
                                "Announcement channel")

    @settings.command(name="roller", description="Turn the web Dice Roller tab on or off.")
    @app_commands.describe(enabled="On or off")
    async def roller(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._apply(interaction, {"dice_roller_enabled": 1 if enabled else 0},
                          f"Web dice roller {'enabled' if enabled else 'disabled'}.")

    @settings.command(name="resonance", description="Set the Resonance table mode.")
    @app_commands.choices(mode=_RESONANCE_CHOICES)
    async def resonance(self, interaction: discord.Interaction,
                        mode: app_commands.Choice[str]) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._apply(interaction, {"resonance_mode": mode.value},
                          f"Resonance table → {mode.name}.")

    @settings.command(name="projects", description="Set the downtime Project mode.")
    @app_commands.choices(mode=_PROJECT_CHOICES)
    async def projects(self, interaction: discord.Interaction,
                       mode: app_commands.Choice[str]) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._apply(interaction, {"project_mode": mode.value},
                          f"Project mode → {mode.name}.")

    @settings.command(name="xp-cap", description="Turn the XP cap on/off and set its amount.")
    @app_commands.describe(enabled="Enforce the cap", amount="Cap amount (optional)")
    async def xp_cap(self, interaction: discord.Interaction, enabled: bool,
                     amount: int | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        fields = {"xp_cap_enabled": 1 if enabled else 0}
        if amount is not None:
            fields["xp_cap_amount"] = amount
        await self._apply(interaction, fields,
                          f"XP cap {'on' if enabled else 'off'}"
                          + (f" at {amount}" if amount is not None else "") + ".")

    @settings.command(name="chars-per-player",
                      description="Max active+pending characters per player (0 = unlimited).")
    @app_commands.describe(count="Number (0–20)")
    async def chars_per_player(self, interaction: discord.Interaction, count: int) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._apply(interaction, {"max_chars_per_player": count},
                          f"Characters per player → {count}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
