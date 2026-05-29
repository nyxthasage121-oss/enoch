"""characters.py — /character slash commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..api import get_character, get_player_characters, upsert_player
from ..config import settings

log = logging.getLogger(__name__)

_GOLD  = 0xC8A85B
_BLOOD = 0x8B1A1A
_MAUVE = 0x7e4ac9


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

    # Merits & Flaws
    merits = [m for m in (sheet.get("merits") or []) if isinstance(m, dict) and m.get("name")]
    flaws  = [f for f in (sheet.get("flaws")  or []) if isinstance(f, dict) and f.get("name")]
    if merits:
        body = "\n".join(f"{_dots(m.get('dots', 0))} {m['name']}" for m in merits)
        e.add_field(name="Merits", value=f"```\n{body}\n```", inline=True)
    if flaws:
        body = "\n".join(f"{_dots(f.get('dots', 0))} {f['name']}" for f in flaws)
        e.add_field(name="Flaws", value=f"```\n{body}\n```", inline=True)

    # Core traits — Humanity / BP / Hunger
    core = (
        f"Humanity      {_dots(sheet.get('humanity', 0), 10)}\n"
        f"Blood Potency {_dots(sheet.get('blood_potency', 0))}\n"
        f"Hunger        {_dots(sheet.get('hunger', 0))}"
    )
    e.add_field(name="Core", value=f"```\n{core}\n```", inline=False)

    # Footer with XP totals
    xp_total = char.get("xp_total", 0)
    xp_cap   = char.get("xp_cap", 350)
    xp_avail = char.get("xp_available", 0)
    e.set_footer(text=f"XP: {xp_total} / {xp_cap}  ·  {xp_avail} available")

    return e


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CharactersCog(bot))
