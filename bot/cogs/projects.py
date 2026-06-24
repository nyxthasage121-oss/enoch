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
    apply_state_delta, get_backgrounds, blank_background,
)
from core.dice import resolve_pool, roll_pool, reroll_failures, blood_surge_bonus
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


def _result_note(proj: dict, result: dict, rolls: dict,
                 extras: list[str] | None = None) -> str:
    """Build the project-specific result text shown under the dice embed."""
    o     = result.get("outcome")
    flags = result.get("flags") or []
    lines = [f"**{proj['title']}**"]
    if o == "bestial":
        lines.append(f"💥 **Bestial failure!** Stage DC rose to "
                     f"**{result.get('stage_dc')}**. Staff will apply a penalty.")
    elif o == "project_complete":
        extra = (" Staff will grant temporary background dots."
                 if "final_temp_dots" in flags else "")
        lines.append(f"✅ **Final stage complete!** Awaiting staff sign-off.{extra}")
    elif o == "stage_complete":
        carry = result.get("carry", 0)
        c = f" (**{carry}** carried over)" if carry else ""
        lines.append(f"▶ **Stage {result.get('stage')} complete!**{c} "
                     f"Now on stage {result.get('next_stage')}.")
    else:
        lines.append(f"Stage {result.get('stage')}: **+{result.get('gained', 0)}** "
                     f"(**{result.get('remaining', 0)}** to the DC).")
    if "messy" in flags and o != "bestial":
        lines.append("⚠️ Messy crit — staff will apply a penalty.")
    if extras:
        lines.append("· " + " · ".join(extras))
    left = max(0, int(rolls.get("remaining", 0)) - 1)
    lines.append(f"Project rolls left this timeskip: **{left}**")
    return "\n".join(lines)


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
                title = p["title"] + (" · coterie" if p.get("is_coterie") else "")
                e.add_field(name=title, value=_status_line(p), inline=False)
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
            label = (t + " (coterie)") if p.get("is_coterie") else t
            out.append(app_commands.Choice(name=label[:100], value=t[:100]))
            if len(out) >= 25:
                break
        return out

    async def _bg_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest tracked backgrounds with dots free to spend on a project roll."""
        char = await self._one_character(
            interaction, getattr(interaction.namespace, "character", None))
        if not char:
            return []
        try:
            data = await get_backgrounds(char["id"])
        except Exception:
            return []
        cur = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for bg in (data.get("backgrounds") or []):
            nm = (bg.get("name") or "").strip()
            if not nm or int(bg.get("available", 0)) <= 0:
                continue
            if cur and cur not in nm.lower():
                continue
            out.append(app_commands.Choice(
                name=f"{nm} (+{bg.get('available', 0)} dice)"[:100], value=nm[:100]))
            if len(out) >= 25:
                break
        return out

    @project.command(name="roll",
                     description="Roll a downtime extended-test project for tonight")
    @app_commands.describe(
        project="Which roll project (if you have more than one active)",
        surge="Blood surge this roll: +1 Hunger for extra dice",
        willpower="Spend 2 Willpower to reroll up to 3 failed dice",
        background="Spend a tracked Background for bonus dice (blanks it this timeskip)",
        teamwork="Bonus dice from teamwork (Allies / Mawla / Retainers)",
        adversary="Dice subtracted by an Enemy / Adversary",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(project=_roll_autocomplete, background=_bg_autocomplete)
    async def project_roll(self, interaction: discord.Interaction,
                           project: str | None = None,
                           surge: bool = False,
                           willpower: bool = False,
                           background: str | None = None,
                           teamwork: app_commands.Range[int, 0, 15] = 0,
                           adversary: app_commands.Range[int, 0, 15] = 0,
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
        if expr.isdigit():
            total, parts, unknown = int(expr), [], []
        else:
            total, parts, unknown = resolve_pool(expr, sheet, _TRAIT_INDEX)
        hunger = int(sheet.get("hunger", 0) or 0)

        surge_dice = 0
        if surge:
            surge_dice = blood_surge_bonus(int(sheet.get("blood_potency", 0) or 0))
            total += surge_dice
            hunger = min(5, hunger + 1)        # a project blood surge is a flat +1 Hunger

        # Background support: spend a tracked Background for bonus dice equal to
        # its available dots (it gets blanked for the rest of the timeskip).
        bg_bonus, bg_used = 0, None
        if background:
            try:
                bdata = await get_backgrounds(char["id"])
                bg = next((b for b in (bdata.get("backgrounds") or [])
                           if b["name"].lower() == background.strip().lower()), None)
            except Exception:
                bg = None
            if bg and int(bg.get("available", 0)) > 0:
                bg_bonus, bg_used = int(bg["available"]), bg["name"]
        tw  = int(teamwork)
        adv = int(adversary)
        total = max(0, total + bg_bonus + tw - adv)

        result = roll_pool(total, hunger, 0)
        rerolled = 0
        if willpower:
            result, rerolled = reroll_failures(result.normal_dice, result.hunger_dice, 0)
        hunger_one = any(d == 1 for d in result.hunger_dice)

        # Resolve web-side against the current stage (re-validates owner + budget).
        resp = await record_project_roll(
            proj["id"], str(interaction.user.id), result.successes, result.outcome,
            critical=result.critical, messy=result.messy,
            hunger_one=hunger_one, pool_size=result.pool)
        if resp.get("error"):
            await interaction.followup.send(f"❌ {resp['error']}", ephemeral=True)
            return

        # Only spend Hunger / WP once the roll has actually counted.
        if surge:
            try:
                await apply_state_delta(char["id"], hunger=1, source="project blood surge")
            except Exception as exc:
                log.warning("surge state delta failed for %s: %s", char.get("id"), exc)
        if willpower:
            try:
                await apply_state_delta(char["id"], damage_willpower_sup=2,
                                        source="project WP reroll")
            except Exception as exc:
                log.warning("WP state delta failed for %s: %s", char.get("id"), exc)
        if bg_used and bg_bonus:
            try:
                await blank_background(char["id"], bg_used, bg_bonus)
            except Exception as exc:
                log.warning("project bg blank failed for %s: %s", char.get("id"), exc)

        extras = []
        if surge_dice:
            extras.append(f"surged +{surge_dice} (+1 Hunger)")
        if rerolled:
            extras.append(f"WP reroll ({rerolled} dice, −2 WP)")
        if bg_bonus:
            extras.append(f"+{bg_bonus} from {bg_used} (blanked)")
        if tw:
            extras.append(f"+{tw} teamwork")
        if adv:
            extras.append(f"−{adv} adversary")
        note  = _result_note(proj, resp.get("result") or {}, rolls, extras=extras)
        embed = build_roll_embed(result, title=f"📜 {proj['title']} — downtime roll",
                                 pool_parts=parts, unknown=unknown, note=note)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectsCog(bot))
