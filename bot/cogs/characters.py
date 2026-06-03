"""characters.py — /character slash commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import (
    get_character, get_player_characters, upsert_player, set_condition, set_bond,
    get_backgrounds, blank_background,
)
from ..config import settings

log = logging.getLogger(__name__)

_GOLD  = 0xC8A85B
_BLOOD = 0x8B1A1A
_MAUVE = 0x7e4ac9

# Common V5 statuses suggested by the /condition add autocomplete. Players can
# still type a custom condition — this is a convenience list, not a whitelist.
_COMMON_CONDITIONS = [
    "Torpor", "In Frenzy", "Terror Frenzy", "Hunger Frenzy", "On Fire",
    "Staked", "In Daysleep", "Blush of Life", "Hidden", "Hunted",
    "Grappled", "Impaired", "Drained", "Sunlight Exposure", "Diablerie Mark",
]


def _web(path: str) -> str:
    return settings.WEB_URL.rstrip("/") + path


# ── V5 trait labels for the /character sheet embed ────────────────────────────
# Kept in sync with web/v5_traits.py but duplicated to keep the bot independent.

_ATTRIBUTES = [
    ("Physical", [
        ("attr_strength",     "Strength"),
        ("attr_dexterity",    "Dexterity"),
        ("attr_stamina",      "Stamina"),
    ]),
    ("Social", [
        ("attr_charisma",     "Charisma"),
        ("attr_manipulation", "Manipulation"),
        ("attr_composure",    "Composure"),
    ]),
    ("Mental", [
        ("attr_intelligence", "Intelligence"),
        ("attr_wits",         "Wits"),
        ("attr_resolve",      "Resolve"),
    ]),
]

_SKILLS_BY_CAT = {
    "Physical": [
        ("skill_athletics",     "Athletics"),
        ("skill_brawl",         "Brawl"),
        ("skill_craft",         "Craft"),
        ("skill_drive",         "Drive"),
        ("skill_firearms",      "Firearms"),
        ("skill_larceny",       "Larceny"),
        ("skill_melee",         "Melee"),
        ("skill_stealth",       "Stealth"),
        ("skill_survival",      "Survival"),
    ],
    "Social": [
        ("skill_animal_ken",    "Animal Ken"),
        ("skill_etiquette",     "Etiquette"),
        ("skill_insight",       "Insight"),
        ("skill_intimidation",  "Intimidation"),
        ("skill_leadership",    "Leadership"),
        ("skill_performance",   "Performance"),
        ("skill_persuasion",    "Persuasion"),
        ("skill_streetwise",    "Streetwise"),
        ("skill_subterfuge",    "Subterfuge"),
    ],
    "Mental": [
        ("skill_academics",     "Academics"),
        ("skill_awareness",     "Awareness"),
        ("skill_finance",       "Finance"),
        ("skill_investigation", "Investigation"),
        ("skill_medicine",      "Medicine"),
        ("skill_occult",        "Occult"),
        ("skill_politics",      "Politics"),
        ("skill_science",       "Science"),
        ("skill_technology",    "Technology"),
    ],
}

_DISCIPLINES = [
    ("disc_animalism",          "Animalism"),
    ("disc_auspex",             "Auspex"),
    ("disc_blood_sorcery",      "Blood Sorcery"),
    ("disc_celerity",           "Celerity"),
    ("disc_dominate",           "Dominate"),
    ("disc_fortitude",          "Fortitude"),
    ("disc_obfuscate",          "Obfuscate"),
    ("disc_oblivion",           "Oblivion"),
    ("disc_potence",            "Potence"),
    ("disc_presence",           "Presence"),
    ("disc_protean",            "Protean"),
    ("disc_thin_blood_alchemy", "Thin-Blood Alchemy"),
]

_CLAN_DISCIPLINES = {
    "banu-haqim": {"disc_blood_sorcery", "disc_celerity",      "disc_obfuscate"},
    "brujah":     {"disc_celerity",      "disc_potence",       "disc_presence"},
    "gangrel":    {"disc_animalism",     "disc_fortitude",     "disc_protean"},
    "hecata":     {"disc_auspex",        "disc_fortitude",     "disc_oblivion"},
    "lasombra":   {"disc_dominate",      "disc_oblivion",      "disc_potence"},
    "malkavian":  {"disc_auspex",        "disc_dominate",      "disc_obfuscate"},
    "ministry":   {"disc_obfuscate",     "disc_presence",      "disc_protean"},
    "nosferatu":  {"disc_animalism",     "disc_obfuscate",     "disc_potence"},
    "ravnos":     {"disc_animalism",     "disc_obfuscate",     "disc_presence"},
    "salubri":    {"disc_auspex",        "disc_dominate",      "disc_fortitude"},
    "toreador":   {"disc_auspex",        "disc_celerity",      "disc_presence"},
    "tremere":    {"disc_auspex",        "disc_blood_sorcery", "disc_dominate"},
    "tzimisce":   {"disc_animalism",     "disc_dominate",      "disc_protean"},
    "ventrue":    {"disc_dominate",      "disc_fortitude",     "disc_presence"},
}


def _dots(rating: int, max_dots: int = 5) -> str:
    """Render rating as filled/empty dots, e.g. 3 of 5 -> '●●●○○'."""
    rating = max(0, min(max_dots, rating or 0))
    return "●" * rating + "○" * (max_dots - rating)


def _track(max_boxes: int, superficial: int, aggravated: int) -> str:
    """Render a Health/Willpower track: □ healthy, ▨ superficial, ✖ aggravated.
    Aggravated and superficial are clamped to the box count."""
    max_boxes = max(0, int(max_boxes or 0))
    agg = max(0, min(int(aggravated or 0), max_boxes))
    sup = max(0, min(int(superficial or 0), max_boxes - agg))
    healthy = max_boxes - agg - sup
    return ("□" * healthy + "▨" * sup + "✖" * agg) or "—"


def _format_traits(sheet: dict, traits: list[tuple[str, str]],
                   skip_zero: bool = False, max_dots: int = 5) -> str:
    """Build a code-block list of '<dots> Label' for an embed field."""
    lines: list[str] = []
    for key, label in traits:
        rating = sheet.get(key, 0)
        if skip_zero and not rating:
            continue
        lines.append(f"{_dots(rating, max_dots)} {label}")
    return "\n".join(lines) if lines else "—"


# ── Cog ───────────────────────────────────────────────────────────────────────

class CharactersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    character = app_commands.Group(name="character", description="Character management")

    # ── /character submit ─────────────────────────────────────────────────────

    @character.command(
        name="submit",
        description="Submit a new character for Storyteller approval.",
    )
    async def character_submit(self, interaction: discord.Interaction) -> None:
        """Send the player a link to the Enoch character creation form."""
        discord_id = str(interaction.user.id)
        username   = str(interaction.user)

        # Ensure player profile exists in the tracker
        try:
            await upsert_player(discord_id, username)
        except Exception as exc:
            log.warning("Player upsert failed for %s: %s", discord_id, exc)

        e = discord.Embed(
            title="📋 Create a Character",
            description=(
                "Fill out the character creation form on **Enoch** to submit your character "
                "for Storyteller review.\n\n"
                "You'll receive a DM here once your character has been approved or returned "
                "with feedback."
            ),
            color=_GOLD,
        )
        e.set_footer(text="Sign in with Discord — your account is already linked.")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open Character Form",
            style=discord.ButtonStyle.link,
            url=_web("/characters/new"),
            emoji="🩸",
        ))

        await interaction.response.send_message(embed=e, view=view, ephemeral=True)

    # ── /character list ───────────────────────────────────────────────────────

    @character.command(
        name="list",
        description="View your characters and their XP status.",
    )
    async def character_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)

        try:
            characters = await get_player_characters(discord_id)
        except Exception as exc:
            log.warning("character list failed for %s: %s", discord_id, exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True,
            )
            return

        if not characters:
            e = discord.Embed(
                title="No Characters on Record",
                description=(
                    "You haven't submitted a character yet.\n"
                    "Use `/character submit` to create one."
                ),
                color=_BLOOD,
            )
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Create a Character",
                style=discord.ButtonStyle.link,
                url=_web("/characters/new"),
                emoji="🩸",
            ))
            await interaction.followup.send(embed=e, view=view, ephemeral=True)
            return

        embeds: list[discord.Embed] = []
        for char in characters[:10]:
            clan  = char.get("clan", "").replace("-", " ").title()
            total = char.get("xp_total", 0)
            cap   = char.get("xp_cap", 350) or 350
            avail = char.get("xp_available", 0)
            pct   = round(total / cap * 100) if cap else 0

            if not char.get("is_approved"):
                status_str = "⏳ Pending Approval"
                color = 0x7e4ac9
            elif char.get("status") == "active":
                status_str = "🩸 Active"
                color = _BLOOD if total >= cap else _GOLD
            elif char.get("status") == "retired":
                status_str = "📜 Retired"
                color = 0x666666
            else:
                status_str = char.get("status", "Unknown").title()
                color = _BLOOD

            e = discord.Embed(title=char["name"], color=color)
            e.add_field(name="Clan",      value=clan or "—",     inline=True)
            e.add_field(name="Status",    value=status_str,       inline=True)

            if char.get("is_approved"):
                e.add_field(name="XP Available", value=f"**{avail}**",            inline=True)
                e.add_field(name="XP Earned",    value=f"{total} / {cap} ({pct}%)", inline=False)

            if total >= cap:
                e.set_footer(text="⚠️ XP cap reached — speak with staff about retirement.")
            elif char.get("predator_type"):
                e.set_footer(text=char["predator_type"])

            embeds.append(e)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open Enoch",
            style=discord.ButtonStyle.link,
            url=_web("/characters"),
            emoji="🩸",
        ))

        await interaction.followup.send(embeds=embeds, view=view, ephemeral=True)

    # ── /character sheet ──────────────────────────────────────────────────────

    @character.command(
        name="sheet",
        description="Show a character's full sheet — attributes, skills, disciplines, and more.",
    )
    @app_commands.describe(name="Character name (only required if you have more than one)")
    async def character_sheet(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        try:
            characters = await get_player_characters(discord_id)
        except Exception as exc:
            log.warning("character sheet failed for %s: %s", discord_id, exc)
            await interaction.followup.send(
                "❌ Could not reach the tracker right now. Try again in a moment.",
                ephemeral=True,
            )
            return

        active = [c for c in characters if c["is_approved"]]
        if not active:
            await interaction.followup.send(
                "You have no approved characters. Use `/character submit` to create one.",
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
                    f"No approved character named **{name}** found. "
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
                f"Try `/character sheet name:<...>`.\n\nOptions: {names}",
                ephemeral=True,
            )
            return

        # Fetch the full character (with sheet_json) — list endpoint may not include sheet
        try:
            char = await get_character(target["id"])
        except Exception as exc:
            log.warning("get_character failed for %d: %s", target["id"], exc)
            await interaction.followup.send(
                "❌ Could not load the sheet. Try again in a moment.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=_build_sheet_embed(char), ephemeral=True)

    # ── /condition (add / clear / list) ───────────────────────────────────────

    condition = app_commands.Group(
        name="condition",
        description="Track transient statuses on a character (torpor, frenzy, …)")

    async def _one_character(self, interaction: discord.Interaction,
                             name: str | None) -> dict | None:
        """Resolve the invoking player's approved character by name, or their
        only one. Returns None when it can't pick."""
        try:
            chars = await get_player_characters(str(interaction.user.id))
        except Exception as exc:
            log.warning("_one_character failed for %s: %s", interaction.user.id, exc)
            return None
        active = [c for c in chars if c.get("is_approved")]
        if name:
            return next((c for c in active
                         if c["name"].lower() == name.strip().lower()), None)
        return active[0] if len(active) == 1 else None

    async def _condition_add_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        cur = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for nm in _COMMON_CONDITIONS:
            if cur and cur not in nm.lower():
                continue
            out.append(app_commands.Choice(name=nm, value=nm))
        return out

    async def _condition_clear_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest the character's CURRENT conditions for clearing."""
        char = await self._one_character(
            interaction, getattr(interaction.namespace, "character", None))
        if not char:
            return []
        try:
            full = await get_character(char["id"])
        except Exception:
            return []
        sheet = _parse_sheet(full)
        cur = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for c in (sheet.get("conditions") or []):
            if not isinstance(c, dict):
                continue
            nm = (c.get("name") or "").strip()
            if not nm or (cur and cur not in nm.lower()):
                continue
            out.append(app_commands.Choice(name=nm[:100], value=nm[:100]))
            if len(out) >= 25:
                break
        return out

    @condition.command(name="add", description="Add a status/condition to a character")
    @app_commands.describe(
        name="Condition (torpor, on fire, staked, …)",
        note="Optional detail (e.g. 'until staff wakes')",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(name=_condition_add_autocomplete)
    async def condition_add(self, interaction: discord.Interaction, name: str,
                            note: str | None = None,
                            character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            resp = await set_condition(char["id"], name.strip(), note=note,
                                       active=True)
        except Exception as exc:
            log.warning("condition add failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send(
                "❌ Could not update conditions.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=_conditions_embed(char["name"], resp.get("conditions") or [],
                                    highlight=name.strip(), added=True),
            ephemeral=True)

    @condition.command(name="clear", description="Clear a status/condition from a character")
    @app_commands.describe(
        name="Condition to clear",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(name=_condition_clear_autocomplete)
    async def condition_clear(self, interaction: discord.Interaction, name: str,
                              character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            resp = await set_condition(char["id"], name.strip(), active=False)
        except Exception as exc:
            log.warning("condition clear failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send(
                "❌ Could not update conditions.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=_conditions_embed(char["name"], resp.get("conditions") or [],
                                    highlight=name.strip(), added=False),
            ephemeral=True)

    @condition.command(name="list", description="List a character's active conditions")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def condition_list(self, interaction: discord.Interaction,
                             character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send(
                "❌ Could not load the sheet.", ephemeral=True)
            return
        conds = _parse_sheet(full).get("conditions") or []
        await interaction.followup.send(
            embed=_conditions_embed(char["name"], conds), ephemeral=True)

    # ── /blank (background blanking) ───────────────────────────────────────────

    async def _background_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest the character's tracked backgrounds that still have dots free."""
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
            if not nm or (cur and cur not in nm.lower()):
                continue
            label = f"{nm} ({bg.get('available', 0)}/{bg.get('dots', 0)} free)"[:100]
            out.append(app_commands.Choice(name=label, value=nm[:100]))
            if len(out) >= 25:
                break
        return out

    @app_commands.command(
        name="blank",
        description="Blank dots of a background for tonight (restores next night)")
    @app_commands.describe(
        background="Which tracked background to blank",
        dots="How many dots to blank",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(background=_background_autocomplete)
    async def blank(self, interaction: discord.Interaction, background: str,
                    dots: app_commands.Range[int, 1, 10] = 1,
                    character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            resp = await blank_background(char["id"], background.strip(), dots)
        except Exception as exc:
            log.warning("blank failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send(
                "❌ Could not blank that background.", ephemeral=True)
            return
        if resp.get("error"):
            await interaction.followup.send(f"❌ {resp['error']}", ephemeral=True)
            return
        r = resp.get("result") or {}
        e = discord.Embed(
            title="🌑 Background Blanked",
            description=(
                f"Blanked **{r.get('blanked_now', dots)}** dot(s) of "
                f"**{r.get('name', background)}** for **{char['name']}**."
            ),
            color=_BLOOD,
        )
        e.add_field(name="Available now",
                    value=f"{r.get('available', 0)}/{r.get('dots', 0)}", inline=True)
        e.add_field(name="Restores", value="when the next night opens", inline=True)
        await interaction.followup.send(embed=e, ephemeral=True)

    # ── /bond (drink / set / clear / list) ────────────────────────────────────

    bond = app_commands.Group(
        name="bond",
        description="Track blood bonds this character holds toward regnants")

    async def _bond_regnant_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest the character's CURRENT regnants (for re-drinking/clearing)."""
        char = await self._one_character(
            interaction, getattr(interaction.namespace, "character", None))
        if not char:
            return []
        try:
            full = await get_character(char["id"])
        except Exception:
            return []
        cur = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for b in (_parse_sheet(full).get("bonds") or []):
            if not isinstance(b, dict):
                continue
            nm = (b.get("regnant") or "").strip()
            if not nm or (cur and cur not in nm.lower()):
                continue
            lvl = int(b.get("level", 0) or 0)
            out.append(app_commands.Choice(name=f"{nm} ({lvl}/6)"[:100], value=nm[:100]))
            if len(out) >= 25:
                break
        return out

    @bond.command(name="drink",
                  description="Drink from a regnant — deepen the bond by one (toward 3)")
    @app_commands.describe(
        regnant="Whose blood you drank (a vampire's name)",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(regnant=_bond_regnant_autocomplete)
    async def bond_drink(self, interaction: discord.Interaction, regnant: str,
                         character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            resp = await set_bond(char["id"], regnant.strip(), delta=1)
        except Exception as exc:
            log.warning("bond drink failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send(
                "❌ Could not update bonds.", ephemeral=True)
            return
        bonds = resp.get("bonds") or []
        lvl = next((int(b.get("level", 0)) for b in bonds
                    if b.get("regnant", "").strip().lower() == regnant.strip().lower()),
                   0)
        status = _bond_status(lvl)
        suffix = f" · **{status}**" if status else ""
        note = f"Drank from {regnant.strip()} — bond now **{lvl}/6**{suffix}."
        await interaction.followup.send(
            embed=_bonds_embed(char["name"], bonds, note=note), ephemeral=True)

    @bond.command(name="set", description="Set a bond's level directly (0 clears it)")
    @app_commands.describe(
        regnant="The regnant's name",
        level="Bond strength 0-6 (3 = full bond, 6 = max; 0 removes it)",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(regnant=_bond_regnant_autocomplete)
    async def bond_set(self, interaction: discord.Interaction, regnant: str,
                       level: app_commands.Range[int, 0, 6],
                       character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            resp = await set_bond(char["id"], regnant.strip(), level=int(level))
        except Exception as exc:
            log.warning("bond set failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send(
                "❌ Could not update bonds.", ephemeral=True)
            return
        note = (f"Cleared the bond to {regnant.strip()}." if level == 0
                else f"Set the bond to {regnant.strip()} at **{level}/6**.")
        await interaction.followup.send(
            embed=_bonds_embed(char["name"], resp.get("bonds") or [], note=note),
            ephemeral=True)

    @bond.command(name="clear", description="Remove a blood bond entirely")
    @app_commands.describe(
        regnant="The regnant to break the bond with",
        character="Which character (only if you have more than one)")
    @app_commands.autocomplete(regnant=_bond_regnant_autocomplete)
    async def bond_clear(self, interaction: discord.Interaction, regnant: str,
                         character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            resp = await set_bond(char["id"], regnant.strip(), level=0)
        except Exception as exc:
            log.warning("bond clear failed for %s: %s", char.get("id"), exc)
            await interaction.followup.send(
                "❌ Could not update bonds.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=_bonds_embed(char["name"], resp.get("bonds") or [],
                               note=f"Broke the bond to {regnant.strip()}."),
            ephemeral=True)

    @bond.command(name="list", description="List this character's blood bonds")
    @app_commands.describe(character="Which character (only if you have more than one)")
    async def bond_list(self, interaction: discord.Interaction,
                        character: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        char = await self._one_character(interaction, character)
        if not char:
            await interaction.followup.send(
                "Pick a character with `character:<name>`.", ephemeral=True)
            return
        try:
            full = await get_character(char["id"])
        except Exception:
            await interaction.followup.send(
                "❌ Could not load the sheet.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=_bonds_embed(char["name"], _parse_sheet(full).get("bonds") or []),
            ephemeral=True)


def _parse_sheet(char: dict) -> dict:
    """Pull a parsed sheet_json dict off a character payload."""
    sheet = char.get("sheet_json") or {}
    if isinstance(sheet, str):
        import json
        try:
            sheet = json.loads(sheet)
        except Exception:
            sheet = {}
    return sheet


def _conditions_embed(char_name: str, conditions: list, *,
                      highlight: str | None = None,
                      added: bool | None = None) -> discord.Embed:
    """Render a character's active conditions."""
    clean = [c for c in conditions if isinstance(c, dict) and c.get("name")]
    if clean:
        body = "\n".join(
            f"• **{c['name']}**" + (f" — {c['note']}" if c.get("note") else "")
            for c in clean)
    else:
        body = "_No active conditions._"
    e = discord.Embed(title=f"🌫️ Conditions · {char_name}", description=body,
                      color=_MAUVE)
    if highlight and added is True:
        e.set_footer(text=f"Added: {highlight}")
    elif highlight and added is False:
        e.set_footer(text=f"Cleared: {highlight}")
    return e


def _bond_status(level: int) -> str:
    """Short status for a bond level on the NYbN 1-6 scale: 3 dots is a full
    bond (3 drinks on separate nights within a year); 6 is the maximum."""
    if level >= 6:
        return "maximum bond"
    if level >= 3:
        return "fully bonded"
    return ""


def _bonds_embed(char_name: str, bonds: list, *,
                 note: str | None = None) -> discord.Embed:
    """Render a character's blood bonds (dots out of 6; 3 is a full bond,
    6 is the max)."""
    clean = [b for b in bonds if isinstance(b, dict) and b.get("regnant")]
    if clean:
        clean.sort(key=lambda b: -int(b.get("level", 0) or 0))
        lines = []
        for b in clean:
            lvl = max(0, min(6, int(b.get("level", 0) or 0)))
            status = _bond_status(lvl)
            tag = f"  · {status}" if status else ""
            lines.append(f"{_dots(lvl, 6)} **{b['regnant']}**{tag}")
        body = "\n".join(lines)
    else:
        body = "_No blood bonds._"
    e = discord.Embed(title=f"🩸 Blood Bonds · {char_name}", description=body,
                      color=_BLOOD)
    if note:
        e.set_footer(text=note)
    return e


def _build_sheet_embed(char: dict) -> discord.Embed:
    """Render a character's sheet as a Discord embed."""
    sheet = char.get("sheet_json") or {}
    if isinstance(sheet, str):
        import json
        try: sheet = json.loads(sheet)
        except Exception: sheet = {}

    clan      = (char.get("clan") or "").lower()
    clan_pretty = (char.get("clan") or "").replace("-", " ").title()
    color     = _BLOOD if (char.get("xp_total") or 0) >= (char.get("xp_cap") or 350) else _GOLD

    desc_lines = [f"**{clan_pretty}**"]
    if char.get("predator_type"):
        desc_lines[0] += f" · {char['predator_type']}"
    if char.get("concept"):
        desc_lines.append(f"_{char['concept']}_")

    e = discord.Embed(
        title=f"🩸 {char['name']}",
        description="\n".join(desc_lines),
        color=color,
    )

    # Attributes — all 9, always shown
    for cat, traits in _ATTRIBUTES:
        e.add_field(name=cat, value=f"```\n{_format_traits(sheet, traits)}\n```", inline=True)

    # Skills — only non-zero, grouped by category
    has_skill = False
    for cat in ("Physical", "Social", "Mental"):
        traits = _SKILLS_BY_CAT[cat]
        body = _format_traits(sheet, traits, skip_zero=True)
        if body != "—":
            has_skill = True
            # Add specialties under each skill
            specs_by_key = {}
            for s in (sheet.get("specialties") or []):
                if isinstance(s, dict):
                    specs_by_key.setdefault(s.get("skill"), []).append(s.get("name", ""))
            lines = []
            for key, label in traits:
                rating = sheet.get(key, 0)
                if not rating:
                    continue
                line = f"{_dots(rating)} {label}"
                if specs_by_key.get(key):
                    line += "  · " + ", ".join(s for s in specs_by_key[key] if s)
                lines.append(line)
            e.add_field(
                name=f"Skills — {cat}",
                value="```\n" + "\n".join(lines) + "\n```",
                inline=False,
            )
    if not has_skill:
        e.add_field(name="Skills", value="_None set._", inline=False)

    # Disciplines — only non-zero, with "(clan)" tag
    clan_set = _CLAN_DISCIPLINES.get(clan, set())
    disc_lines = []
    for key, label in _DISCIPLINES:
        rating = sheet.get(key, 0)
        if not rating:
            continue
        tag = " (clan)" if key in clan_set else ""
        disc_lines.append(f"{_dots(rating)} {label}{tag}")
    if disc_lines:
        e.add_field(
            name="Disciplines",
            value="```\n" + "\n".join(disc_lines) + "\n```",
            inline=False,
        )

    # Advantages (Merits / Advantages / Backgrounds are one pool here) & Flaws
    advs = [a for key in ("merits", "advantages", "backgrounds")
            for a in (sheet.get(key) or [])
            if isinstance(a, dict) and a.get("name")]
    flaws = [f for f in (sheet.get("flaws") or [])
             if isinstance(f, dict) and f.get("name")]
    if advs:
        body = "\n".join(f"{_dots(a.get('dots', 0))} {a['name']}" for a in advs)
        e.add_field(name="Advantages", value=f"```\n{body}\n```", inline=True)
    if flaws:
        body = "\n".join(f"{_dots(f.get('dots', 0))} {f['name']}" for f in flaws)
        e.add_field(name="Flaws", value=f"```\n{body}\n```", inline=True)

    # Learned Discipline powers + Blood Sorcery / Oblivion / Alchemy rites.
    powers = [p for p in (sheet.get("powers") or [])
              if isinstance(p, dict) and p.get("name")]
    if powers:
        body = "\n".join(f"L{p.get('level', 1)} {p['name']}" for p in powers)
        e.add_field(name="Powers", value=f"```\n{body}\n```", inline=False)
    rites = []
    for key, tag in (("rituals", "Ritual"), ("ceremonies", "Ceremony"),
                     ("formulae", "Formula")):
        for it in (sheet.get(key) or []):
            if isinstance(it, dict) and it.get("name"):
                rites.append(f"L{it.get('level', 1)} {it['name']} · {tag}")
    if rites:
        e.add_field(name="Rituals & Rites",
                    value="```\n" + "\n".join(rites) + "\n```", inline=False)

    # Core traits — Health / Willpower tracks + Humanity / BP / Hunger.
    health_max = (sheet.get("attr_stamina", 0) or 0) + 3
    wp_max = (sheet.get("attr_composure", 0) or 0) + (sheet.get("attr_resolve", 0) or 0)
    core = (
        f"Health        {_track(health_max, sheet.get('damage_health_sup', 0), sheet.get('damage_health_agg', 0))}\n"
        f"Willpower     {_track(wp_max, sheet.get('damage_willpower_sup', 0), sheet.get('damage_willpower_agg', 0))}\n"
        f"Humanity      {_dots(sheet.get('humanity', 0), 10)}\n"
        f"Blood Potency {_dots(sheet.get('blood_potency', 0))}\n"
        f"Hunger        {_dots(sheet.get('hunger', 0))}"
    )
    e.add_field(name="Core", value=f"```\n{core}\n```", inline=False)

    # Transient conditions (set via /condition) — only when present.
    conditions = [c for c in (sheet.get("conditions") or [])
                  if isinstance(c, dict) and c.get("name")]
    if conditions:
        body = "\n".join(
            f"• {c['name']}" + (f" — {c['note']}" if c.get("note") else "")
            for c in conditions)
        e.add_field(name="Conditions", value=body, inline=False)

    # Blood bonds (set via /bond) — dots out of 3; only when present.
    bonds = [b for b in (sheet.get("bonds") or [])
             if isinstance(b, dict) and b.get("regnant")]
    if bonds:
        bonds = sorted(bonds, key=lambda b: -int(b.get("level", 0) or 0))
        body = "\n".join(
            f"{_dots(max(0, min(6, int(b.get('level', 0) or 0))), 6)} {b['regnant']}"
            for b in bonds)
        e.add_field(name="Blood Bonds", value=f"```\n{body}\n```", inline=False)

    # Footer with XP totals
    xp_total = char.get("xp_total", 0)
    xp_cap   = char.get("xp_cap", 350)
    xp_avail = char.get("xp_available", 0)
    e.set_footer(text=f"XP: {xp_total} / {xp_cap}  ·  {xp_avail} available")

    return e


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CharactersCog(bot))
