"""Smoke test for the /character sheet embed builder.

This is offline-only — no Discord connection. Verifies the embed structure,
field layout, and dot rendering.
"""
import os

# bot/config.py reads these at import time and crashes on empty values
os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS",   "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from bot.cogs.characters import (  # noqa: E402
    _build_sheet_embed, _dots, _conditions_embed, _bonds_embed,
)


def test_dots_render():
    assert _dots(0)  == "○○○○○"
    assert _dots(3)  == "●●●○○"
    assert _dots(5)  == "●●●●●"
    assert _dots(7, 10) == "●●●●●●●○○○"
    # Out-of-range gets clamped
    assert _dots(99, 5) == "●●●●●"
    assert _dots(-3, 5) == "○○○○○"


def test_embed_structure_full_sheet():
    char = {
        "id": 1, "name": "Valeria Morano", "clan": "brujah", "predator_type": "Siren",
        "concept": "Former NYC DA turned revolutionary",
        "xp_total": 6, "xp_cap": 350, "xp_available": 3,
        "sheet_json": {
            "attr_strength": 3, "attr_dexterity": 2, "attr_stamina": 2,
            "skill_athletics": 3, "skill_investigation": 4,
            "disc_potence": 2, "disc_presence": 1,
            "humanity": 7, "blood_potency": 1, "hunger": 2,
            "specialties": [{"skill": "skill_athletics", "name": "Parkour"}],
            "merits": [{"name": "Resources", "dots": 3}],
            "flaws":  [{"name": "Stake Bait", "dots": 1}],
        },
    }
    e = _build_sheet_embed(char)
    assert e.title == "🩸 Valeria Morano"
    assert "Brujah" in e.description
    assert "Siren" in e.description
    assert "revolutionary" in e.description
    assert "XP: 6 / 350" in e.footer.text
    field_names = [f.name for f in e.fields]
    # Attributes: 3 fields (Physical/Social/Mental)
    assert "Physical" in field_names
    assert "Social"   in field_names
    assert "Mental"   in field_names
    # Skills only render rows that exist
    assert any(n.startswith("Skills") for n in field_names)
    # Disciplines only show non-zero
    assert "Disciplines" in field_names
    # Advantages (merits/advantages/backgrounds pooled) + Flaws
    assert "Advantages" in field_names
    assert "Flaws"  in field_names
    # Core always present
    assert "Core" in field_names


def test_embed_marks_clan_disciplines():
    char = {
        "id": 1, "name": "V", "clan": "brujah",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {
            "disc_potence": 2,   # Brujah clan
            "disc_auspex":  1,   # Out of clan
        },
    }
    e = _build_sheet_embed(char)
    disc_field = next(f for f in e.fields if f.name == "Disciplines")
    assert "Potence (clan)" in disc_field.value
    assert "Auspex" in disc_field.value
    assert "Auspex (clan)" not in disc_field.value


def test_embed_shows_health_and_willpower_tracks():
    char = {
        "id": 1, "name": "Marcus", "clan": "ventrue",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {
            "attr_stamina": 3, "attr_composure": 2, "attr_resolve": 3,
            "damage_health_sup": 2, "damage_health_agg": 1,
            "damage_willpower_sup": 1,
            "humanity": 7, "blood_potency": 1, "hunger": 2,
        },
    }
    e = _build_sheet_embed(char)
    core = next(f for f in e.fields if f.name == "Core").value
    assert "Health" in core and "Willpower" in core
    # Health = Stamina 3 + 3 = 6 boxes; 2 superficial + 1 aggravated → 3 healthy.
    assert "□□□▨▨✖" in core
    # Willpower = Composure 2 + Resolve 3 = 5 boxes; 1 superficial → 4 healthy.
    assert "□□□□▨" in core


def test_embed_pools_advantages_and_lists_powers_and_rites():
    char = {
        "id": 1, "name": "Tarik", "clan": "tremere",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {
            "merits":      [{"name": "Beautiful", "dots": 1}],
            "advantages":  [{"name": "Allies", "dots": 2}],
            "backgrounds": [{"name": "Resources", "dots": 3}],
            "powers":      [{"discipline": "disc_auspex", "name": "Heightened Senses",
                             "level": 1}],
            "rituals":     [{"name": "Wake with Evening's Freshness", "level": 1}],
        },
    }
    e = _build_sheet_embed(char)
    fields = {f.name: f.value for f in e.fields}
    # All three advantage lists pooled under one "Advantages" field.
    assert "Advantages" in fields
    adv = fields["Advantages"]
    assert "Beautiful" in adv and "Allies" in adv and "Resources" in adv
    # Powers + rites surfaced.
    assert "Heightened Senses" in fields.get("Powers", "")
    assert "Wake with Evening's Freshness" in fields.get("Rituals & Rites", "")
    assert "Ritual" in fields.get("Rituals & Rites", "")


def test_embed_shows_conditions_when_present():
    char = {
        "id": 1, "name": "Lucian", "clan": "gangrel",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {
            "conditions": [
                {"name": "Torpor", "note": "until staff wakes"},
                {"name": "On Fire"},
            ],
        },
    }
    e = _build_sheet_embed(char)
    cond = next((f for f in e.fields if f.name == "Conditions"), None)
    assert cond is not None
    assert "Torpor" in cond.value and "until staff wakes" in cond.value
    assert "On Fire" in cond.value


def test_embed_omits_conditions_when_absent():
    char = {
        "id": 1, "name": "Pending", "clan": "malkavian",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {},
    }
    e = _build_sheet_embed(char)
    assert "Conditions" not in [f.name for f in e.fields]


def test_conditions_embed_helper_renders_and_footers():
    e = _conditions_embed("Marcus", [{"name": "Staked"}], highlight="Staked",
                          added=True)
    assert "Marcus" in e.title
    assert "Staked" in e.description
    assert e.footer.text == "Added: Staked"
    # Empty list shows a placeholder, clear footer.
    e2 = _conditions_embed("Marcus", [], highlight="Staked", added=False)
    assert "No active conditions" in e2.description
    assert e2.footer.text == "Cleared: Staked"


def test_embed_shows_blood_bonds_when_present():
    char = {
        "id": 1, "name": "Cecile", "clan": "toreador",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {
            "bonds": [
                {"regnant": "Prince Antoine", "level": 3},
                {"regnant": "Sire Marguerite", "level": 1},
            ],
        },
    }
    e = _build_sheet_embed(char)
    bonds = next((f for f in e.fields if f.name == "Blood Bonds"), None)
    assert bonds is not None
    assert "Prince Antoine" in bonds.value and "Sire Marguerite" in bonds.value
    # Full bond (3) renders three filled dots and sorts first.
    assert bonds.value.index("Antoine") < bonds.value.index("Marguerite")
    assert "●●●" in bonds.value


def test_bonds_embed_helper_full_and_empty():
    e = _bonds_embed("Cecile", [{"regnant": "Antoine", "level": 3}],
                     note="Drank from Antoine — bond now 3/3.")
    assert "Antoine" in e.description
    assert "full bond" in e.description
    assert e.footer.text.startswith("Drank from Antoine")
    e2 = _bonds_embed("Cecile", [])
    assert "No blood bonds" in e2.description


def test_embed_handles_empty_sheet():
    char = {
        "id": 1, "name": "Pending Kindred", "clan": "malkavian",
        "xp_total": 0, "xp_cap": 350, "xp_available": 0,
        "sheet_json": {},
    }
    e = _build_sheet_embed(char)
    field_names = [f.name for f in e.fields]
    # Attributes always shown
    assert "Physical" in field_names
    # Skills empty placeholder
    assert "Skills" in field_names
    # No discipline panel when nothing set
    assert "Disciplines" not in field_names
    # No merits / flaws panels
    assert "Merits" not in field_names
    assert "Flaws"  not in field_names
    # Core still rendered
    assert "Core" in field_names
