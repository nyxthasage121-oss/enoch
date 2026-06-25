"""settings.py — `/settings` interactive chronicle-settings menu.

One `/settings` command opens a single menu (Discord Components V2) of toggles +
dropdowns + channel pickers that reflect the current chronicle settings and change
them in place — modelled on tiltowait/inconnu's `/settings` UX (MIT). Every write
goes through the web API, which is the single authority and enforces the
Settings-Admin gate: non-admins see the menu, but its controls are disabled.

Setting a channel from Discord is the win — pick it from the dropdown right where
you are, no snowflake-copying. Complex config (tier budgets, chargen, predator
unlocks, staff roles) stays on the web.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_chronicle_settings, update_chronicle_settings

log = logging.getLogger(__name__)

_GOLD = 0xC8A85B

# Mirror web/settings_enums.py (the bot can't import the web package; the web
# re-validates every value, so any drift is a polite error, not a bad write).
_RESONANCE = [
    ("standard", "Standard — V5 core"),
    ("tattered_facade", "Tattered Facade — alt Disciplines"),
    ("add_empty", "Add Empty — +1-in-6 Empty"),
]
_PROJECT = [
    ("nybn", "NYbN — multi-stage extended test"),
    ("homebrew", "Homebrew — staff-set goal"),
    ("off", "Off — Projects disabled"),
]
_XP_AMOUNTS = [150, 200, 250, 300, 350, 400, 450, 500]
_CHAR_CAPS = [0, 1, 2, 3, 4, 5, 6, 8, 10]


def _chan_label(guild, raw) -> str:
    """Human label for a stored channel id — a mention when we can resolve it."""
    raw = str(raw or "").strip()
    if not raw.isdigit():
        return "—"
    ch = guild.get_channel(int(raw)) if guild else None
    return ch.mention if ch else f"<#{raw}>"


class SettingsView(discord.ui.LayoutView):
    """The interactive chronicle-settings menu. Rebuilt from the API's settings
    dict after every change, so the controls always show live state. Controls are
    disabled unless ``data['editable']`` (the web says the user is Settings-Admin)."""

    def __init__(self, data: dict, *, actor_id: str, guild) -> None:
        super().__init__(timeout=300)
        self.data = data
        self.actor_id = actor_id
        self.guild = guild
        editable = bool(data.get("editable"))

        c = discord.ui.Container(accent_colour=_GOLD)
        note = ("Change any control to update it instantly." if editable
                else "🔒 You need the **Settings Admin** flag to change these.")
        c.add_item(discord.ui.TextDisplay(f"## ⚙️ Chronicle Settings\n{note}"))

        # ── Toggles (two buttons share a row) ──
        roller_on = bool(data.get("dice_roller_enabled"))
        roller = discord.ui.Button(
            label=f"Web Dice Roller: {'On' if roller_on else 'Off'}",
            style=discord.ButtonStyle.success if roller_on else discord.ButtonStyle.secondary,
            disabled=not editable)
        roller.callback = self._toggle_roller
        cap_on = bool(data.get("xp_cap_enabled"))
        cap_amt = int(data.get("xp_cap_amount", 350) or 350)
        cap = discord.ui.Button(
            label=f"XP Cap: {('On · ' + str(cap_amt)) if cap_on else 'Off'}",
            style=discord.ButtonStyle.success if cap_on else discord.ButtonStyle.secondary,
            disabled=not editable)
        cap.callback = self._toggle_cap
        c.add_item(discord.ui.ActionRow(roller, cap))

        # ── Enum + number dropdowns ──
        c.add_item(discord.ui.ActionRow(self._enum_select(
            "Resonance table", _RESONANCE, data.get("resonance_mode", "standard"),
            editable, self._set_resonance)))
        c.add_item(discord.ui.ActionRow(self._enum_select(
            "Project mode", _PROJECT, data.get("project_mode", "nybn"),
            editable, self._set_project)))
        c.add_item(discord.ui.ActionRow(self._num_select(
            "XP cap amount", _XP_AMOUNTS, cap_amt, editable, self._set_cap_amount)))
        c.add_item(discord.ui.ActionRow(self._num_select(
            "Characters per player", _CHAR_CAPS,
            int(data.get("max_chars_per_player", 2) or 2), editable, self._set_chars,
            zero_label="0 — unlimited")))

        # ── Channels — current value shown above each picker; deselect to clear ──
        for field, label, cb in (
            ("dice_channel_id", "Dice post channel", self._set_dice_channel),
            ("st_channel_id", "ST tracker channel", self._set_st_channel),
            ("announce_channel_id", "Announcement channel", self._set_announce_channel),
        ):
            c.add_item(discord.ui.TextDisplay(
                f"**{label}:** {_chan_label(guild, data.get(field))}"))
            picker = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                placeholder=f"Set {label} (or clear)",
                min_values=0, max_values=1, disabled=not editable)
            picker.callback = cb
            c.add_item(discord.ui.ActionRow(picker))

        self.add_item(c)

    # ── component builders ──
    def _enum_select(self, label, choices, current, editable, callback):
        opts = [discord.SelectOption(label=text, value=val, default=(val == current))
                for val, text in choices]
        sel = discord.ui.Select(placeholder=f"{label}: {current}", options=opts,
                                disabled=not editable)
        sel.callback = callback
        return sel

    def _num_select(self, label, values, current, editable, callback, zero_label=None):
        vals = sorted(set(list(values) + [current]))
        opts = []
        for v in vals:
            text = zero_label if (zero_label and v == 0) else str(v)
            opts.append(discord.SelectOption(label=text, value=str(v), default=(v == current)))
        sel = discord.ui.Select(placeholder=f"{label}: {current}", options=opts,
                                disabled=not editable)
        sel.callback = callback
        return sel

    # ── persistence + live refresh ──
    async def _apply(self, interaction: discord.Interaction, fields: dict) -> None:
        result = await update_chronicle_settings(self.actor_id, fields)
        if result.get("error"):
            await interaction.response.send_message(f"❌ {result['error']}", ephemeral=True)
            return
        result["editable"] = True   # they just edited → they're a Settings Admin
        await interaction.response.edit_message(
            view=SettingsView(result, actor_id=self.actor_id, guild=self.guild))

    @staticmethod
    def _picked(interaction: discord.Interaction) -> str:
        """The first selected value from a select/channel-select interaction, or
        '' when nothing is selected (used to clear a channel)."""
        vals = (interaction.data or {}).get("values") or []
        return vals[0] if vals else ""

    # ── callbacks ──
    async def _toggle_roller(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"dice_roller_enabled": 0 if self.data.get("dice_roller_enabled") else 1})

    async def _toggle_cap(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"xp_cap_enabled": 0 if self.data.get("xp_cap_enabled") else 1})

    async def _set_resonance(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"resonance_mode": self._picked(interaction)})

    async def _set_project(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"project_mode": self._picked(interaction)})

    async def _set_cap_amount(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"xp_cap_amount": int(self._picked(interaction) or 0)})

    async def _set_chars(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"max_chars_per_player": int(self._picked(interaction) or 0)})

    async def _set_dice_channel(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"dice_channel_id": self._picked(interaction)})

    async def _set_st_channel(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"st_channel_id": self._picked(interaction)})

    async def _set_announce_channel(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, {"announce_channel_id": self._picked(interaction)})


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="settings",
        description="View + change chronicle settings (Settings-Admin to change).")
    @app_commands.guild_only()
    async def settings(self, interaction: discord.Interaction) -> None:
        try:
            data = await get_chronicle_settings(actor=str(interaction.user.id))
        except Exception as exc:
            log.warning("settings: fetch failed: %s", exc)
            await interaction.response.send_message(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True)
            return
        await interaction.response.send_message(
            view=SettingsView(data, actor_id=str(interaction.user.id), guild=interaction.guild),
            ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
