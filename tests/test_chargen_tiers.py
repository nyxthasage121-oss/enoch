"""Chargen 'gambit': create a character for every type + Kindred tier through
the real POST /characters/new endpoint and verify the seeded Blood Potency /
Humanity / Hunger match V5 RAW (the Sea-of-Time per-tier seeding from the recent
RAW pass — the validator tests cover the spread rules but not the result).
"""
import json as _j

import pytest


def _raw_traits(clan="brujah"):
    """RAW-valid base allocation for a creation POST: the 4/3/3/3/2/2/2/2/1
    attributes, a Balanced skill spread, the free specialties, 7 advantage dots
    + 2 flaw dots, and the clan's 2+1 in-clan Discipline base."""
    attrs = {
        "attr_strength": "4", "attr_dexterity": "3", "attr_stamina": "3",
        "attr_charisma": "3", "attr_manipulation": "2", "attr_composure": "2",
        "attr_intelligence": "2", "attr_wits": "2", "attr_resolve": "1",
    }
    skills = {  # Balanced: three 3s, five 2s, seven 1s
        "skill_brawl": "3", "skill_athletics": "3", "skill_stealth": "3",
        "skill_melee": "2", "skill_firearms": "2", "skill_larceny": "2",
        "skill_streetwise": "2", "skill_intimidation": "2",
        "skill_awareness": "1", "skill_drive": "1", "skill_occult": "1",
        "skill_academics": "1", "skill_insight": "1", "skill_persuasion": "1",
        "skill_subterfuge": "1",
    }
    advantages = {
        "backgrounds": _j.dumps([{"name": "Allies", "dots": 3},
                                 {"name": "Resources", "dots": 2}]),
        "merits":      _j.dumps([{"name": "Iron Will", "dots": 2}]),
        "advantages":  _j.dumps([]),
        "flaws":       _j.dumps([{"name": "Enemy", "dots": 1},
                                 {"name": "Disliked", "dots": 1}]),
        "specialties": _j.dumps([{"skill": "skill_academics", "name": "History"},
                                 {"skill": "skill_brawl", "name": "Grappling"}]),
    }
    from web.v5_traits import CLAN_DISCIPLINES
    disc = {}
    _inclan = CLAN_DISCIPLINES.get(clan)
    if _inclan and len(_inclan) >= 2:
        disc = {_inclan[0]: "2", _inclan[1]: "1"}
    return {**attrs, **skills, "skill_spread": "balanced", **disc, **advantages}


@pytest.fixture(autouse=True)
def _standard_ruleset(player):
    # Ancilla goes through the In Memoriam era builder when the chronicle runs
    # the in_memoriam ruleset; force standard so the tier shortcut applies.
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, active_ruleset="standard")
    return player


def _create(player, *, name, clan="brujah", character_type="kindred",
            character_tier="neonate"):
    data = {
        "_csrf": "dev-csrf-token", "name": name, "clan": clan,
        "character_type": character_type, "character_tier": character_tier,
        "merits": "[]", "flaws": "[]", "powers": "[]", "rituals": "[]",
        "ceremonies": "[]", "formulae": "[]", "convictions": "[]",
        "touchstones": _j.dumps(["Sister Maria", "Father Joseph"]),
        **_raw_traits("brujah"),
    }
    # Ghouls take at most 1 Discipline dot at creation, not the Kindred 2+1
    # spread the helper seeds — strip them so the ghoul validates.
    if character_type == "ghoul":
        for k in [k for k in data if k.startswith("disc_")]:
            data[k] = 0
    return player.post("/characters/new", data=data, follow_redirects=False)


def _sheet_of(name):
    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name=? ORDER BY id DESC LIMIT 1", (name,)
        ).fetchone()
    return row, (_j.loads(row["sheet_json"]) if row else None)


def _cleanup(cid):
    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM characters WHERE id=?", (cid,))


@pytest.mark.parametrize("tier,bp,hum", [
    ("fledgling", 1, 7),
    ("neonate",   1, 7),
    ("thinblood", 0, 7),
    ("ancilla",   2, 6),   # the recently-fixed case (was understated as BP1/Hum7)
])
def test_kindred_tier_seeds_bp_humanity(player, tier, bp, hum):
    name = f"GambitKindred_{tier}"
    r = _create(player, name=name, character_tier=tier)
    assert r.status_code == 303, f"{tier} create did not succeed (got {r.status_code})"
    row, sheet = _sheet_of(name)
    assert row is not None, f"{tier} character was not created"
    try:
        assert sheet["blood_potency"] == bp, f"{tier}: BP {sheet.get('blood_potency')} != {bp}"
        assert sheet["humanity"] == hum, f"{tier}: Humanity {sheet.get('humanity')} != {hum}"
        assert sheet["hunger"] == 1
    finally:
        _cleanup(row["id"])


def test_mortal_has_no_bp_or_hunger(player):
    r = _create(player, name="GambitMortal", clan="", character_type="mortal")
    assert r.status_code == 303
    row, sheet = _sheet_of("GambitMortal")
    try:
        assert "blood_potency" not in sheet
        assert "hunger" not in sheet
        assert sheet["humanity"] == 7
    finally:
        _cleanup(row["id"])


def test_ghoul_has_humanity_no_hunger(player):
    r = _create(player, name="GambitGhoul", clan="", character_type="ghoul")
    assert r.status_code == 303
    row, sheet = _sheet_of("GambitGhoul")
    try:
        assert "hunger" not in sheet
        assert sheet["humanity"] == 7
    finally:
        _cleanup(row["id"])
