"""`/roll` — a Vampire: The Masquerade 5e dice roller wired to character sheets.

UX modeled on the familiar Inconnu commands (`/roll strength + brawl`), but the
V5 mechanics are implemented natively in ``bot/roll.py``. The pool can be a raw
number or a trait expression resolved from the invoking player's character;
Hunger defaults to that character's current Hunger.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_character, get_player_characters
from ..roll import (
    build_trait_index, resolve_pool, roll_pool, OUTCOME_LABELS,
    MESSY_CRITICAL, BESTIAL_FAILURE, TOTAL_FAILURE, FAILURE, RollResult,
)
from .characters import _ATTRIBUTES, _SKILLS_BY_CAT, _DISCIPLINES

log = logging.getLogger(__name__)

_GOLD = 0xC29B48
_BLOOD = 0x8B1A1A

# Build the trait name → sheet key index once, from the same label maps the
# sheet embed uses (attributes, all skills, disciplines).
_TRAIT_INDEX = build_trait_index(
    [pair for _cat, traits in _ATTRIBUTES for pair in traits],
    [pair for traits in _SKILLS_BY_CAT.values() for pair in traits],
    _DISCIPLINES,
)

# Outcome → icon for the result headline.
_OUTCOME_ICON = {
    "critical":        "✦",
    MESSY_CRITICAL:    "🩸",
    "success":         "◆",
    FAILURE:           "✕",
    TOTAL_FAILURE:     "✕",
    BESTIAL_FAILURE:   "🐺",
}


def _fmt_dice(dice: list[int], *, hunger: bool = False) -> str:
    """Render dice faces, bolding 10s (criticals) and underlining Hunger 1s
    (bestial markers). Markdown renders inside embed fields."""
    if not dice:
        return "—"
    out = []
    for d in dice:
        if d == 10:
            out.append(f"**{d}**")
        elif hunger and d == 1:
            out.append(f"__{d}__")
        else:
            out.append(str(d))
    return " · ".join(out)


def build_roll_embed(result: RollResult, *, title: str,
                     pool_parts: list[tuple[str, int]] | None = None,
                     unknown: list[str] | None = None) -> discord.Embed:
    """Render a roll result as a Discord embed (offline-testable)."""
    color = _BLOOD if (result.outcome in (MESSY_CRITICAL, BESTIAL_FAILURE)
                       or not result.is_win) else _GOLD

    icon = _OUTCOME_ICON.get(result.outcome, "◆")
    e = discord.Embed(
        title=f"{icon} {title}",
        description=f"**{OUTCOME_LABELS[result.outcome]}**",
        color=color,
    )

    e.add_field(name="Dice", value=_fmt_dice(result.normal_dice), inline=False)
    if result.hunger:
        e.add_field(name="Hunger", value=_fmt_dice(result.hunger_dice, hunger=True),
                    inline=False)

    succ = f"{result.successes} success" + ("" if result.successes == 1 else "es")
    if result.difficulty:
        succ += f"  vs difficulty {result.difficulty}  ·  margin {result.margin:+d}"
    e.add_field(name="Result", value=succ, inline=False)

    footer_bits = []
    if pool_parts:
        footer_bits.append(
            "Pool: " + " + ".join(f"{lbl} {val}" for lbl, val in pool_parts)
            + f" = {result.pool}d"
        )
    if unknown:
        footer_bits.append("Unknown: " + ", ".join(unknown))
    if footer_bits:
        e.set_footer(text="   ".join(footer_bits))
    return e


def _looks_numeric(expr: str) -> bool:
    """True when the whole pool expression is a single non-negative integer."""
    return expr.strip().isdigit()


class RollCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="roll",
        description="Roll a V5 dice pool — a number or traits like 'strength + brawl'.",
    )
    @app_commands.describe(
        pool="A number (e.g. 5) or trait expression (e.g. strength + brawl + 1)",
        difficulty="Successes needed (optional)",
        hunger="Override Hunger dice (defaults to your character's Hunger)",
        character="Which character (only if you have more than one)",
    )
    async def roll(
        self,
        interaction: discord.Interaction,
        pool: str,
        difficulty: int = 0,
        hunger: int | None = None,
        character: str | None = None,
    ) -> None:
        await interaction.response.defer()

        # A bare numeric pool with an explicit Hunger needs no character lookup.
        if _looks_numeric(pool) and hunger is not None:
            result = roll_pool(int(pool), hunger, difficulty)
            await interaction.followup.send(
                embed=build_roll_embed(result, title=f"Roll · {pool}d"))
            return

        # Otherwise resolve the invoking player's character for traits + Hunger.
        discord_id = str(interaction.user.id)
        try:
            characters = await get_player_characters(discord_id)
        except Exception as exc:
            log.warning("roll: get_player_characters failed for %s: %s", discord_id, exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.")
            return

        active = [c for c in characters if c.get("is_approved")]
        char = None
        if character:
            char = next((c for c in active
                         if c["name"].lower() == character.strip().lower()), None)
            if not char:
                await interaction.followup.send(
                    f"No approved character named **{character}** found.")
                return
        elif len(active) == 1:
            char = active[0]
        elif _looks_numeric(pool):
            # Raw numeric roll, no character context needed.
            result = roll_pool(int(pool), hunger or 0, difficulty)
            await interaction.followup.send(
                embed=build_roll_embed(result, title=f"Roll · {pool}d"))
            return
        else:
            names = ", ".join(f"`{c['name']}`" for c in active) or "(none)"
            await interaction.followup.send(
                "Specify which character with `character:<name>`. "
                f"Your characters: {names}")
            return

        try:
            full = await get_character(char["id"])
        except Exception as exc:
            log.warning("roll: get_character failed for %d: %s", char["id"], exc)
            await interaction.followup.send("❌ Could not load your sheet. Try again shortly.")
            return

        sheet = full.get("sheet_json") or {}
        if isinstance(sheet, str):
            import json
            try:
                sheet = json.loads(sheet)
            except Exception:
                sheet = {}

        # Resolve the pool: a bare number rolls as-is; traits resolve from sheet.
        if _looks_numeric(pool):
            total, parts, unknown = int(pool), [(pool, int(pool))], []
        else:
            total, parts, unknown = resolve_pool(pool, sheet, _TRAIT_INDEX)

        eff_hunger = hunger if hunger is not None else int(sheet.get("hunger", 0) or 0)
        result = roll_pool(total, eff_hunger, difficulty)
        await interaction.followup.send(
            embed=build_roll_embed(result, title=full["name"],
                                   pool_parts=parts, unknown=unknown))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollCog(bot))
