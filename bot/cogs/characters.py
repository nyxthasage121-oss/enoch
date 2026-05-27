"""characters.py — /character slash commands."""
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..api import create_character, upsert_player

log = logging.getLogger(__name__)

_GOLD  = 0xC8A85B
_BLOOD = 0x8B1A1A


def _slugify(s: str) -> str:
    """Normalise clan input to kebab-case slug (e.g. 'Banu Haqim' → 'banu-haqim')."""
    return re.sub(r"\s+", "-", s.strip().lower())


# ── Modal ─────────────────────────────────────────────────────────────────────

class CharacterSubmitModal(discord.ui.Modal, title="Submit a New Character"):
    char_name = discord.ui.TextInput(
        label="Character Name",
        placeholder="e.g. Elara Moreau",
        max_length=100,
        required=True,
    )
    clan = discord.ui.TextInput(
        label="Clan",
        placeholder="e.g. Ventrue, Tremere, Banu Haqim, Caitiff…",
        max_length=60,
        required=True,
    )
    predator_type = discord.ui.TextInput(
        label="Predator Type (optional)",
        placeholder="e.g. Alleycat, Cleaver, Osiris, Sandman…",
        max_length=60,
        required=False,
    )
    concept = discord.ui.TextInput(
        label="Concept (optional)",
        placeholder="e.g. Fallen Detective, Ambitious Neonate…",
        max_length=120,
        required=False,
    )
    sire = discord.ui.TextInput(
        label="Sire (optional)",
        placeholder="Your sire's name",
        max_length=100,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        username   = str(interaction.user)

        # Ensure player profile exists (best-effort — create_character also upserts)
        try:
            await upsert_player(discord_id, username)
        except Exception:
            pass

        name    = self.char_name.value.strip()
        clan_in = self.clan.value.strip()

        try:
            char = await create_character({
                "discord_id":    discord_id,
                "name":          name,
                "clan":          _slugify(clan_in),
                "predator_type": self.predator_type.value.strip() or None,
                "concept":       self.concept.value.strip() or None,
                "sire":          self.sire.value.strip() or None,
                "username":      username,
            })
        except Exception as exc:
            log.warning("Character submit failed for %s: %s", discord_id, exc)
            await interaction.followup.send(
                "❌ Submission failed. Please try again or contact a Storyteller.",
                ephemeral=True,
            )
            return

        e = discord.Embed(
            title="📋 Character Submitted",
            description=(
                f"**{char['name']}** has been submitted for staff review.\n\n"
                "You'll receive a DM once your character is approved or returned."
            ),
            color=_GOLD,
        )
        e.add_field(name="Clan",   value=clan_in, inline=True)
        if self.predator_type.value.strip():
            e.add_field(name="Predator Type", value=self.predator_type.value.strip(), inline=True)
        e.set_footer(text="Status: Pending Review")
        await interaction.followup.send(embed=e, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("CharacterSubmitModal error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Something went wrong. Please try again.", ephemeral=True
            )


# ── Cog ───────────────────────────────────────────────────────────────────────

class CharactersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    character = app_commands.Group(name="character", description="Character management")

    @character.command(
        name="submit",
        description="Submit a new character for Storyteller approval.",
    )
    async def character_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CharacterSubmitModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CharactersCog(bot))
