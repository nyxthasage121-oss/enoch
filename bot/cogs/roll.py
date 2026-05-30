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

from ..api import (
    get_character, get_player_characters, apply_state_delta, set_macro,
)
from ..roll import (
    build_trait_index, resolve_pool, apply_specialty, roll_pool,
    reroll_failures, rouse_check, blood_surge_bonus, mend_amount,
    willpower_recovery, bane_severity, frenzy_pool, remorse_pool,
    OUTCOME_LABELS, MESSY_CRITICAL, BESTIAL_FAILURE, TOTAL_FAILURE, FAILURE,
    RollResult,
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
# skill key → label, for the specialty picker display.
_SKILL_LABEL = {k: lbl for traits in _SKILLS_BY_CAT.values() for k, lbl in traits}

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
                     unknown: list[str] | None = None,
                     note: str | None = None) -> discord.Embed:
    """Render a roll result as a Discord embed (offline-testable)."""
    color = _BLOOD if (result.outcome in (MESSY_CRITICAL, BESTIAL_FAILURE)
                       or not result.is_win) else _GOLD

    icon = _OUTCOME_ICON.get(result.outcome, "◆")
    e = discord.Embed(
        title=f"{icon} {title}",
        description=f"**{OUTCOME_LABELS[result.outcome]}**",
        color=color,
    )

    if note:
        e.add_field(name="Blood Surge", value=note, inline=False)
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


class WillpowerRerollView(discord.ui.View):
    """A one-shot "Reroll (Willpower)" button on a roll result. Rerolls up to
    three regular (non-Hunger) failures for the original roller only."""

    def __init__(self, result: RollResult, *, title: str,
                 pool_parts=None, unknown=None, user_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self._result = result
        self._title = title
        self._pool_parts = pool_parts
        self._unknown = unknown
        self._user_id = user_id

    @discord.ui.button(label="Reroll (Willpower)", style=discord.ButtonStyle.secondary)
    async def reroll(self, interaction: discord.Interaction,
                     button: discord.ui.Button) -> None:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message(
                "Only the original roller can spend Willpower on this roll.",
                ephemeral=True)
            return
        new_result, n = reroll_failures(
            self._result.normal_dice, self._result.hunger_dice,
            self._result.difficulty)
        button.disabled = True   # Willpower reroll is once per roll
        embed = build_roll_embed(
            new_result, title=f"{self._title} · Willpower reroll",
            pool_parts=self._pool_parts, unknown=self._unknown)
        note = f"Rerolled {n} die{'s' if n != 1 else ''} with Willpower"
        base = embed.footer.text or ""
        embed.set_footer(text=(base + "   " if base else "") + note)
        await interaction.response.edit_message(embed=embed, view=self)


async def _reply_roll(interaction: discord.Interaction, result: RollResult, *,
                      title: str, pool_parts=None, unknown=None,
                      note: str | None = None) -> None:
    """Send a roll result, attaching the Willpower-reroll button when there are
    regular failures worth rerolling."""
    embed = build_roll_embed(result, title=title, pool_parts=pool_parts,
                             unknown=unknown, note=note)
    view = None
    if any(d < 6 for d in result.normal_dice):
        view = WillpowerRerollView(result, title=title, pool_parts=pool_parts,
                                   unknown=unknown, user_id=interaction.user.id)
    await interaction.followup.send(embed=embed, view=view)


def _looks_numeric(expr: str) -> bool:
    """True when the whole pool expression is a single non-negative integer."""
    return expr.strip().isdigit()


def _sheet_of(full: dict) -> dict:
    """Pull a parsed sheet_json dict off a character payload."""
    sheet = full.get("sheet_json") or {}
    if isinstance(sheet, str):
        import json
        try:
            sheet = json.loads(sheet)
        except Exception:
            sheet = {}
    return sheet


class RollCog(commands.Cog):
    macro = app_commands.Group(
        name="macro", description="Saved roll pools — use them with /roll <name>")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _specialty_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest the invoking player's character specialties for the +1
        specialty die. Each choice value is shaped 'skill_key:Name'."""
        char = await self._pick_character(
            interaction, getattr(interaction.namespace, "character", None))
        if not char:
            return []
        try:
            full = await get_character(char["id"])
        except Exception:
            return []
        sheet = full.get("sheet_json") or {}
        if isinstance(sheet, str):
            import json
            try:
                sheet = json.loads(sheet)
            except Exception:
                sheet = {}
        cur = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for s in (sheet.get("specialties") or []):
            if not isinstance(s, dict):
                continue
            nm = (s.get("name") or "").strip()
            sk = s.get("skill") or ""
            if not nm:
                continue
            label = f"{_SKILL_LABEL.get(sk, sk.replace('skill_', '').title())} · {nm}"
            if cur and cur not in label.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=f"{sk}:{nm}"[:100]))
            if len(out) >= 25:
                break
        return out

    @app_commands.command(
        name="roll",
        description="Roll a V5 dice pool — a number or traits like 'strength + brawl'.",
    )
    @app_commands.describe(
        pool="Number (5), traits (strength + brawl), or a saved macro name",
        difficulty="Successes needed (optional)",
        hunger="Override Hunger dice (defaults to your character's Hunger)",
        specialty="Add a +1 specialty die (pick one of your character's specialties)",
        surge="Blood Surge: spend a Rouse Check to add dice (scales with Blood Potency)",
        character="Which character (only if you have more than one)",
    )
    @app_commands.autocomplete(specialty=_specialty_autocomplete)
    async def roll(
        self,
        interaction: discord.Interaction,
        pool: str,
        difficulty: int = 0,
        hunger: int | None = None,
        specialty: str | None = None,
        surge: bool = False,
        character: str | None = None,
    ) -> None:
        await interaction.response.defer()

        # A bare numeric pool with an explicit Hunger needs no character lookup
        # (unless surging — that needs Blood Potency + the live Hunger).
        if _looks_numeric(pool) and hunger is not None and not surge:
            await _reply_roll(interaction, roll_pool(int(pool), hunger, difficulty),
                              title=f"Roll · {pool}d")
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
        elif _looks_numeric(pool) and not surge:
            # Raw numeric roll, no character context needed.
            await _reply_roll(interaction, roll_pool(int(pool), hunger or 0, difficulty),
                              title=f"Roll · {pool}d")
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

        sheet = _sheet_of(full)

        # Expand a saved macro name into its stored pool expression.
        macros = sheet.get("macros") or {}
        if pool.strip() in macros:
            pool = macros[pool.strip()]

        # Resolve the pool: a bare number rolls as-is; traits resolve from sheet.
        if _looks_numeric(pool):
            total, parts, unknown = int(pool), [(pool, int(pool))], []
        else:
            total, parts, unknown = resolve_pool(pool, sheet, _TRAIT_INDEX)

        # +1 specialty die when the player picked one their character owns.
        total, parts, unknown = apply_specialty(
            total, parts, unknown, specialty, sheet.get("specialties"))

        eff_hunger = hunger if hunger is not None else int(sheet.get("hunger", 0) or 0)

        # Blood Surge — add dice by Blood Potency at the cost of a Rouse Check.
        # The Rouse may raise Hunger, which both feeds this roll's Hunger dice
        # and persists to the sheet.
        surge_note = None
        if surge:
            bonus = blood_surge_bonus(sheet.get("blood_potency", 0))
            total += bonus
            parts = parts + [("Blood Surge", bonus)]
            rolls, gained = rouse_check(1)
            new_hunger = min(5, eff_hunger + gained)
            if gained > 0:
                try:
                    resp = await apply_state_delta(char["id"], hunger=gained,
                                                   source="dice:surge")
                    new_hunger = resp.get("state", {}).get("hunger", new_hunger)
                except Exception as exc:
                    log.warning("surge: hunger write-back failed for %s: %s",
                                char["id"], exc)
            eff_hunger = new_hunger
            rouse_txt = (f"+{gained} Hunger → {new_hunger}/5" if gained
                         else "no Hunger gained")
            surge_note = f"+{bonus} dice · Rouse {_fmt_dice(rolls)} → {rouse_txt}"

        result = roll_pool(total, eff_hunger, difficulty)
        await _reply_roll(interaction, result, title=full["name"],
                          pool_parts=parts, unknown=unknown, note=surge_note)

    @app_commands.command(
        name="rouse",
        description="Make a Rouse Check — test for Hunger gain (updates your sheet).",
    )
    @app_commands.describe(
        count="How many Rouse Checks (e.g. a level-2 power costs 2)",
        character="Which character (only if you have more than one)",
    )
    async def rouse(self, interaction: discord.Interaction, count: int = 1,
                    character: str | None = None) -> None:
        await interaction.response.defer()
        rolls, gained = rouse_check(count)

        # Resolve the player's character so we can apply the Hunger gain live.
        char = await self._pick_character(interaction, character)

        new_hunger = None
        if char and gained > 0:
            try:
                resp = await apply_state_delta(char["id"], hunger=gained,
                                               source="dice:rouse")
                new_hunger = resp.get("state", {}).get("hunger")
            except Exception as exc:
                log.warning("rouse: hunger write-back failed for %s: %s",
                            char.get("id"), exc)

        color = _GOLD if gained == 0 else _BLOOD
        if gained == 0:
            desc = "**No Hunger gained.**"
        elif new_hunger is not None:
            desc = f"**+{gained} Hunger** → now **{new_hunger}/5**"
        else:
            desc = f"**+{gained} Hunger.**"
        e = discord.Embed(
            title=f"🩸 Rouse Check{f' · {char['name']}' if char else ''}",
            description=desc, color=color)
        e.add_field(name="Dice", value=_fmt_dice(sorted(rolls, reverse=True)),
                    inline=False)
        foot = "6+ avoids Hunger · 1-5 gains 1"
        if gained > 0 and new_hunger is None:
            foot += " · update your Hunger on the tracker"
        e.set_footer(text=foot)
        await interaction.followup.send(embed=e)

    # ── Saved macros ─────────────────────────────────────────────────────────

    @macro.command(name="save", description="Save a named roll pool")
    @app_commands.describe(
        name="What to call it (e.g. frenzy)",
        pool="Pool expression, e.g. strength + brawl",
        character="Which character (only if you have more than one)",
    )
    async def macro_save(self, interaction: discord.Interaction, name: str,
                         pool: str, character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            await set_macro(char["id"], name.strip(), pool.strip())
        except Exception as exc:
            log.warning("macro save failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send("❌ Could not save the macro.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Saved **{name.strip()}** = `{pool.strip()}` for {char['name']}. "
            f"Roll it with `/roll {name.strip()}`.", ephemeral=True)

    @macro.command(name="list", description="List your saved roll pools")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def macro_list(self, interaction: discord.Interaction,
                         character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load your sheet.", ephemeral=True)
            return
        macros = _sheet_of(full).get("macros") or {}
        if not macros:
            await interaction.followup.send(
                f"{char['name']} has no saved macros. Save one with `/macro save`.",
                ephemeral=True)
            return
        lines = "\n".join(f"• **{k}** = `{v}`" for k, v in sorted(macros.items()))
        e = discord.Embed(title=f"Macros · {char['name']}", description=lines,
                          color=_GOLD)
        await interaction.followup.send(embed=e, ephemeral=True)

    @macro.command(name="delete", description="Delete a saved roll pool")
    @app_commands.describe(name="Macro to delete",
                           character="Which character (only if you have more than one)")
    async def macro_delete(self, interaction: discord.Interaction, name: str,
                           character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            await set_macro(char["id"], name.strip(), None)
        except Exception:
            await interaction.followup.send("❌ Could not delete the macro.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Deleted **{name.strip()}** (if it existed).", ephemeral=True)

    # ── Nightly routine ──────────────────────────────────────────────────────

    @app_commands.command(
        name="wake",
        description="Wake for the night: a Rouse Check + Willpower recovery.")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def wake(self, interaction: discord.Interaction,
                   character: str | None = None) -> None:
        await interaction.response.defer()
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>` to wake.")
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load your sheet.")
            return
        sheet = _sheet_of(full)
        rolls, gained = rouse_check(1)
        recovery = willpower_recovery(sheet.get("attr_composure", 0),
                                      sheet.get("attr_resolve", 0))
        cur_wp = int(sheet.get("damage_willpower_sup", 0) or 0)
        new_hunger = min(5, int(sheet.get("hunger", 0) or 0) + gained)
        new_wp = max(0, cur_wp - recovery)
        try:
            resp = await apply_state_delta(
                char["id"], hunger=gained, damage_willpower_sup=-recovery,
                source="dice:wake")
            st = resp.get("state", {})
            new_hunger = st.get("hunger", new_hunger)
            new_wp = st.get("damage_willpower_sup", new_wp)
        except Exception as exc:
            log.warning("wake write-back failed for %s: %s", char.get("id"), exc)
        healed = max(0, cur_wp - new_wp)
        e = discord.Embed(title=f"🌙 {char['name']} wakes",
                          color=_GOLD if gained == 0 else _BLOOD)
        e.add_field(name="Waking Rouse",
                    value=f"{_fmt_dice(rolls)} → " +
                          (f"+{gained} Hunger ({new_hunger}/5)" if gained
                           else "no Hunger gained"),
                    inline=False)
        e.add_field(name="Willpower",
                    value=(f"Recovered {healed} Superficial "
                           f"(higher of Composure/Resolve = {recovery})"
                           if recovery else "—"),
                    inline=False)
        await interaction.followup.send(embed=e)

    @app_commands.command(
        name="mend",
        description="Mend Superficial Health — a Rouse Check, by Blood Potency.")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def mend(self, interaction: discord.Interaction,
                   character: str | None = None) -> None:
        await interaction.response.defer()
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>` to mend.")
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load your sheet.")
            return
        sheet = _sheet_of(full)
        amount = mend_amount(sheet.get("blood_potency", 0))
        rolls, gained = rouse_check(1)
        cur_h = int(sheet.get("damage_health_sup", 0) or 0)
        new_hunger = min(5, int(sheet.get("hunger", 0) or 0) + gained)
        new_h = max(0, cur_h - amount)
        try:
            resp = await apply_state_delta(
                char["id"], hunger=gained, damage_health_sup=-amount,
                source="dice:mend")
            st = resp.get("state", {})
            new_hunger = st.get("hunger", new_hunger)
            new_h = st.get("damage_health_sup", new_h)
        except Exception as exc:
            log.warning("mend write-back failed for %s: %s", char.get("id"), exc)
        healed = max(0, cur_h - new_h)
        e = discord.Embed(title=f"🩹 {char['name']} mends",
                          color=_GOLD if gained == 0 else _BLOOD)
        e.add_field(name="Mend",
                    value=f"Healed {healed} Superficial Health "
                          f"(Blood Potency mend = {amount})",
                    inline=False)
        e.add_field(name="Rouse",
                    value=f"{_fmt_dice(rolls)} → " +
                          (f"+{gained} Hunger ({new_hunger}/5)" if gained
                           else "no Hunger gained"),
                    inline=False)
        await interaction.followup.send(embed=e)

    @app_commands.command(
        name="blush",
        description="Blush of Life — a Rouse Check to pass for human this scene.")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def blush(self, interaction: discord.Interaction,
                    character: str | None = None) -> None:
        await interaction.response.defer()
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>` to blush.")
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load your sheet.")
            return
        sheet = _sheet_of(full)
        # Ministry on the "Cold-Blooded" variant Bane: Blush costs Rouse Checks
        # equal to Bane Severity (min 1) and needs recent feeding.
        cold_blooded = ((char.get("clan") or "").lower() == "ministry"
                        and sheet.get("bane_choice") == "variant")
        count = (max(1, bane_severity(sheet.get("blood_potency", 0)))
                 if cold_blooded else 1)
        rolls, gained = rouse_check(count)
        new_hunger = min(5, int(sheet.get("hunger", 0) or 0) + gained)
        if gained > 0:
            try:
                resp = await apply_state_delta(char["id"], hunger=gained,
                                               source="dice:blush")
                new_hunger = resp.get("state", {}).get("hunger", new_hunger)
            except Exception as exc:
                log.warning("blush write-back failed for %s: %s", char.get("id"), exc)
        e = discord.Embed(
            title=f"🌹 Blush of Life · {char['name']}",
            description=f"{char['name']} flushes with borrowed warmth — "
                        "passing for human this scene.",
            color=_GOLD if gained == 0 else _BLOOD)
        e.add_field(
            name=f"Rouse ×{count}" if count > 1 else "Rouse",
            value=f"{_fmt_dice(rolls)} → " +
                  (f"+{gained} Hunger ({new_hunger}/5)" if gained
                   else "no Hunger gained"),
            inline=False)
        if cold_blooded:
            e.set_footer(text="Cold-Blooded: requires recent feeding from a living vessel")
        await interaction.followup.send(embed=e)

    @app_commands.command(
        name="frenzy",
        description="Resist frenzy — a Willpower roll vs the trigger's Difficulty.")
    @app_commands.describe(
        difficulty="Difficulty set by the trigger (e.g. 2-4)",
        character="Which character (only if you have more than one)")
    async def frenzy(self, interaction: discord.Interaction, difficulty: int = 3,
                     character: str | None = None) -> None:
        await interaction.response.defer()
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>` to test frenzy.")
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load your sheet.")
            return
        sheet = _sheet_of(full)
        pool = frenzy_pool(sheet.get("attr_resolve", 0), sheet.get("attr_composure", 0),
                           sheet.get("damage_willpower_sup", 0),
                           sheet.get("damage_willpower_agg", 0))
        result = roll_pool(pool, int(sheet.get("hunger", 0) or 0), max(1, difficulty))
        if result.is_win:
            verdict = "**Resists the frenzy.**"
        elif result.bestial:
            verdict = "**Frenzy! — the Beast slips its leash (bestial failure).**"
        else:
            verdict = "**Succumbs to frenzy.**"
        e = discord.Embed(title=f"🔥 Frenzy Check · {char['name']}",
                          description=verdict,
                          color=_GOLD if result.is_win else _BLOOD)
        e.add_field(name="Willpower dice", value=_fmt_dice(result.normal_dice),
                    inline=False)
        if result.hunger:
            e.add_field(name="Hunger",
                        value=_fmt_dice(result.hunger_dice, hunger=True), inline=False)
        e.add_field(name="Result",
                    value=f"{result.successes} vs difficulty {result.difficulty}"
                          f"  ·  margin {result.margin:+d}", inline=False)
        await interaction.followup.send(embed=e)

    @app_commands.command(
        name="remorse",
        description="Remorse check — resist losing Humanity after gaining Stains.")
    @app_commands.describe(
        stains="How many Stains you took this session",
        character="Which character (only if you have more than one)")
    async def remorse(self, interaction: discord.Interaction, stains: int = 1,
                      character: str | None = None) -> None:
        await interaction.response.defer()
        char = await self._pick_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>` for the Remorse check.")
            return
        if stains <= 0:
            await interaction.followup.send(
                "No Stains this session — no Remorse check needed.")
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send("❌ Could not load your sheet.")
            return
        sheet = _sheet_of(full)
        humanity = int(sheet.get("humanity", 7) or 0)
        pool = remorse_pool(humanity, stains)
        result = roll_pool(pool, 0, 1)   # no Hunger dice; one success suffices
        new_humanity = humanity
        if result.is_win:
            verdict = "**Remorse felt — Humanity holds. Stains clear.**"
        else:
            new_humanity = max(0, humanity - 1)
            try:
                resp = await apply_state_delta(char["id"], humanity=-1,
                                               source="dice:remorse")
                new_humanity = resp.get("state", {}).get("humanity", new_humanity)
            except Exception as exc:
                log.warning("remorse write-back failed for %s: %s", char.get("id"), exc)
            verdict = f"**No remorse — Humanity falls to {new_humanity}.**"
        e = discord.Embed(title=f"🕯️ Remorse · {char['name']}", description=verdict,
                          color=_GOLD if result.is_win else _BLOOD)
        e.add_field(name="Dice", value=_fmt_dice(result.normal_dice), inline=False)
        e.set_footer(
            text=f"{pool} unstained Humanity box{'es' if pool != 1 else ''}"
                 f" · Humanity {humanity} · {stains} stain{'s' if stains != 1 else ''}")
        await interaction.followup.send(embed=e)

    async def _pick_character(self, interaction: discord.Interaction,
                              name: str | None) -> dict | None:
        """Resolve the invoking player's approved character by name, or their
        only one. Returns None silently when it can't pick (the caller decides
        whether that's fatal)."""
        try:
            chars = await get_player_characters(str(interaction.user.id))
        except Exception as exc:
            log.warning("_pick_character failed for %s: %s", interaction.user.id, exc)
            return None
        active = [c for c in chars if c.get("is_approved")]
        if name:
            return next((c for c in active
                         if c["name"].lower() == name.strip().lower()), None)
        return active[0] if len(active) == 1 else None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollCog(bot))
