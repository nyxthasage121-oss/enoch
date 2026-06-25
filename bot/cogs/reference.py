"""reference.py — quick V5 reference lookups in Discord.

`/cripple`, `/probability`, `/resonance` surface Enoch's shared ``core/`` logic
(the same engines the web uses) as Discord commands, for players who'd rather
stay in chat. `/cripple` and `/probability` compute locally (no web needed);
`/resonance` reads the chronicle's configured Resonance table, falling back to
Standard if the tracker is unreachable.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.dice import probability
from core.injury import crippling_injury
from core.resonance import roll_resonance

from ..api import get_chronicle_settings

log = logging.getLogger(__name__)

_GOLD = 0xC8A85B
_BLOOD = 0x8B1A1A


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def build_cripple_embed(result: dict) -> discord.Embed:
    """Render a crippling-injury roll (offline-testable)."""
    e = discord.Embed(
        title="🦴 Crippling Injury",
        description=(f"d10 **{result['die']}** + {result['aggravated']} Aggravated "
                     f"= **{result['roll']}**"),
        color=_BLOOD)
    for inj in result.get("injuries", []):
        e.add_field(name=inj["name"], value=inj["effect"], inline=False)
    if len(result.get("injuries", [])) > 1:
        e.set_footer(text="Two results in this band — the Storyteller picks which applies.")
    return e


def build_probability_embed(o: dict) -> discord.Embed:
    """Render a probability estimate (offline-testable)."""
    desc = f"**{_pct(o['p_success'])}** to succeed"
    if o.get("difficulty"):
        desc += f" vs difficulty {o['difficulty']}"
    desc += f"  ·  ~{o['mean_successes']:.1f} successes avg"
    e = discord.Embed(title="🎲 Odds", description=desc, color=_GOLD)
    e.add_field(name="Pool", value=f"{o['pool']}d · {o['hunger']} Hunger", inline=True)
    e.add_field(name="Critical", value=_pct(o["p_critical"]), inline=True)
    e.add_field(name="Messy", value=_pct(o["p_messy"]), inline=True)
    e.add_field(name="Bestial fail", value=_pct(o["p_bestial"]), inline=True)
    e.set_footer(text=f"Estimated over {o['trials']:,} simulated rolls")
    return e


def build_resonance_embed(rr: dict) -> discord.Embed:
    """Render a generated Resonance + Temperament (offline-testable)."""
    temp = rr.get("temperament_label") or "—"
    if not rr.get("resonance"):
        e = discord.Embed(title="🌑 Resonance", color=_GOLD,
                          description=f"**{temp}** — no usable Resonance tonight.")
        if rr.get("effect"):
            e.set_footer(text=rr["effect"])
        return e
    e = discord.Embed(title="🩸 Resonance",
                      description=f"**{rr.get('label')}** · {temp}", color=_GOLD)
    if rr.get("emotions"):
        e.add_field(name="Emotions", value=rr["emotions"], inline=False)
    if rr.get("disciplines"):
        e.add_field(name="Disciplines", value=", ".join(rr["disciplines"]), inline=False)
    dys = rr.get("dyscrasia")
    if dys:
        e.add_field(name=f"Dyscrasia · {dys.get('name', '')}",
                    value=dys.get("description", ""), inline=False)
    if rr.get("effect"):
        e.set_footer(text=rr["effect"])
    return e


class ReferenceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="cripple",
        description="Roll a V5 crippling injury (d10 + total Aggravated damage).")
    @app_commands.describe(aggravated="Total Aggravated Health damage the character has")
    async def cripple(self, interaction: discord.Interaction, aggravated: int = 0) -> None:
        result = crippling_injury(max(0, aggravated))
        await interaction.response.send_message(embed=build_cripple_embed(result))

    @app_commands.command(
        name="probability",
        description="Odds for a V5 dice pool — success, critical, messy, bestial.")
    @app_commands.describe(
        pool="Number of dice in the pool",
        hunger="Hunger dice (0-5)",
        difficulty="Successes needed (optional)")
    async def probability(self, interaction: discord.Interaction, pool: int,
                          hunger: int = 0, difficulty: int = 0) -> None:
        o = probability(max(0, pool), max(0, hunger), max(0, difficulty))
        await interaction.response.send_message(embed=build_probability_embed(o))

    @app_commands.command(
        name="resonance",
        description="Generate a random Resonance + Temperament (chronicle's table).")
    async def resonance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        mode = "standard"
        try:
            s = await get_chronicle_settings()
            mode = s.get("resonance_mode") or "standard"
        except Exception as exc:
            log.warning("resonance: settings fetch failed, using standard: %s", exc)
        await interaction.followup.send(embed=build_resonance_embed(roll_resonance(mode)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReferenceCog(bot))
