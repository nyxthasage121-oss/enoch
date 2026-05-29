"""Smoke test for the /character sheet embed builder.

This is offline-only — no Discord connection. Verifies the embed structure,
field layout, and dot rendering.
"""
import os

# bot/config.py reads these at import time and crashes on empty values
os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS",   "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from bot.cogs.characters import _build_sheet_embed, _dots   # noqa: E402


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
    # Merits + Flaws
    assert "Merits" in field_names
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
