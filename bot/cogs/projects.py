"""projects.py — /project slash commands (downtime endeavours).

Projects are proposed and managed on the web; roll-based projects (V5 extended
tests) are worked down here. The bot owns the dice: it resolves the pool from
the sheet, rolls, and posts the successes back to the web, which accumulates
them one roll per play period.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import (
    get_character, get_player_characters, get_projects, record_project_roll,
)
from ..roll import resolve_pool, roll_pool
from .characters import _parse_sheet
from .roll import _TRAIT_INDEX, build_roll_embed

log = logging.getLogger(__name__)

_GOLD = 0xC29B48


def _status_line(p: dict) -> str:
    st = p.get("status")
    if st == "proposed":
        return "⏳ Pending staff review"
    if st == "rejected":
        return "✕ Returned by staff"
    if st == "complete":
        reward = p.get("reward_text")
        return "✓ Complete" + (f" — {reward}" if reward else "")
    if p.get("progress_type") == "roll":
        tag = " · **target reached**" if p.get("target_reached") else ""
        return (f"▶ Active — {p.get('progress_successes', 0)}/"
                f"{p.get('target_successes', 0)} successes{tag}")
    return "▶ Active (staff-tracked)"


class ProjectsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    project = app_commands.Group(
        name="project",
        description="Downtime projects — propose on the web, roll them down here")

    async def _one_character(self, interaction: discord.Interaction,
                             name: str | None) -> dict | None:
        try:
            chars = await get_player_characters(str(interaction.user.id))
        except Exception as exc:
            log.warning("project _one_character failed for %s: %s",
                        interaction.user.id, exc)
            return None
        active = [c for c in chars if c.get("is_approved")]
        if name:
            return next((c for c in active
                         if c["name"].lower() == name.strip().lower()), None)
        return active[0] if len(active) == 1 else None

    @project.command(name="list", description="List your character's downtime projects")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def project_list(self, interaction: discord.Interaction,
                           character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            data = await get_projects(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load projects.", ephemeral=True)
            return
        projects = data.get("projects") or []
        e = discord.Embed(title=f"📜 Projects — {char['name']}", color=_GOLD)
        if not projects:
            e.description = "No projects yet. Propose one on your character page."
        else:
            for p in projects[:20]:
                e.add_field(name=p["title"], value=_status_line(p), inline=False)
        rolls = data.get("rolls") or {}
        if rolls.get("period_id"):
            e.set_footer(text=f"Project rolls this timeskip: "
                              f"{rolls.get('remaining', 0)}/{rolls.get('cap', 0)}")
        await interaction.followup.send(embed=e, ephemeral=True)

    async def _roll_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        char = await self._one_character(
            interaction, getattr(interaction.namespace, "character", None))
        if not char:
            return []
        try:
            data = await get_projects(char["id"])
        except Exception:
            return []
        cur = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for p in (data.get("projects") or []):
            if p.get("status") != "active" or p.get("progress_type") != "roll":
                continue
            t = (p.get("title") or "").strip()
            if not t or (cur and cur not in t.lower()):
                continue
            out.append(app_commands.Choice(name=t[:100], value=t[:100]))
            if len(out) >= 25:
                break
        return out

    @project.command(name="roll",
                     description="Roll a downtime extended-test project for tonight")
    @app_commands.describe(
        project="Which roll project (if you have more than one active)",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(project=_roll_autocomplete)
    async def project_roll(self, interaction: discord.Interaction,
                           project: str | None = None,
                           character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            data = await get_projects(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load projects.", ephemeral=True)
            return
        roll_projs = [p for p in (data.get("projects") or [])
                      if p.get("status") == "active" and p.get("progress_type") == "roll"]
        if not roll_projs:
            await interaction.followup.send(
                "You have no active roll-based projects. (Staged projects are "
                "tracked by staff — no roll needed.)", ephemeral=True)
            return
        if project:
            proj = next((p for p in roll_projs
                         if p["title"].lower() == project.strip().lower()), None)
        elif len(roll_projs) == 1:
            proj = roll_projs[0]
        else:
            await interaction.followup.send(
                "You have multiple roll projects — name one with `project:<title>`.",
                ephemeral=True)
            return
        if not proj:
            await interaction.followup.send(
                f"No active roll project named “{project}”.", ephemeral=True)
            return
        rolls = data.get("rolls") or {}
        if not proj.get("can_roll_now"):
            if rolls.get("remaining", 0) <= 0:
                msg = (f"No project rolls left this timeskip "
                       f"({rolls.get('used', 0)}/{rolls.get('cap', 0)} used).")
            else:
                msg = "There's no active timeskip to roll in right now."
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return

        # The bot owns the dice: resolve the pool from the sheet and roll.
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load the sheet.", ephemeral=True)
            return
        sheet = _parse_sheet(full)
        expr  = (proj.get("roll_pool") or "").strip()
        diff  = int(proj.get("roll_difficulty") or 1)
        if expr.isdigit():
            total, parts, unknown = int(expr), [], []
        else:
            total, parts, unknown = resolve_pool(expr, sheet, _TRAIT_INDEX)
        hunger = int(sheet.get("hunger", 0) or 0)
        result = roll_pool(total, hunger, diff)

        # Record the successes web-side (re-validates owner + one-per-period).
        resp = await record_project_roll(
            proj["id"], str(interaction.user.id), result.successes, result.outcome)
        if resp.get("error"):
            await interaction.followup.send(f"❌ {resp['error']}", ephemeral=True)
            return
        updated = resp.get("project") or {}
        prog    = updated.get("progress_successes", 0)
        target  = updated.get("target_successes", 0)
        note    = f"**{proj['title']}** — progress **{prog}/{target}**"
        if updated.get("target_reached"):
            note += "\n**Target reached!** Staff will finalise your reward."
        embed = build_roll_embed(result, title=f"📜 {proj['title']} — downtime roll",
                                 pool_parts=parts, unknown=unknown, note=note)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectsCog(bot))
