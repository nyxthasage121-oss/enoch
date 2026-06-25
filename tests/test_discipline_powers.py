"""Discipline powers catalog (packages/rules/discipline_powers.json), lifted
V5-generic from the friend's data set on 2026-06-17. Validates the catalog
shape and the v5_traits loader/helper."""
from web.v5_traits import DISCIPLINE_POWERS, discipline_powers, V5_DISCIPLINES

_DISC_KEYS = {k for k, _ in V5_DISCIPLINES}


def test_catalog_loads_and_covers_disciplines():
    assert len(DISCIPLINE_POWERS) == 12
    total = sum(len(v) for v in DISCIPLINE_POWERS.values())
    assert total == 244
    # Thin-Blood Alchemy formula catalog brought to V5 parity (2026-06-25).
    assert len(DISCIPLINE_POWERS["disc_thin_blood_alchemy"]) == 48
    for k in DISCIPLINE_POWERS:                 # every key is a real discipline
        assert k in _DISC_KEYS, k


def test_power_shape_is_well_formed():
    for disc, powers in DISCIPLINE_POWERS.items():
        for p in powers:
            assert p["name"] and isinstance(p["name"], str)
            assert 1 <= p["level"] <= 5, (disc, p["name"], p["level"])
            assert isinstance(p["summary"], str)
            assert isinstance(p["dice_pool"], str)
            assert isinstance(p["rouse_checks"], int) and p["rouse_checks"] >= 0
            for a in p.get("amalgam", []):       # amalgam refs are valid keys
                assert a["discipline"] in _DISC_KEYS, (disc, p["name"], a)
                assert 1 <= a["level"] <= 5


def test_powers_sorted_by_level_then_name():
    for disc, powers in DISCIPLINE_POWERS.items():
        keys = [(p["level"], p["name"].lower()) for p in powers]
        assert keys == sorted(keys), disc


def test_helper_filters_by_level():
    all_anim = discipline_powers("disc_animalism")
    assert len(all_anim) == 20
    lvl1 = discipline_powers("disc_animalism", max_level=1)
    assert lvl1 and all(p["level"] == 1 for p in lvl1)
    assert len(lvl1) < len(all_anim)
    assert discipline_powers("nonexistent") == []


def test_known_powers_present():
    names = {p["name"] for p in discipline_powers("disc_animalism")}
    assert {"Bond Famulus", "Feral Whispers", "Sense the Beast"} <= names


def test_chargen_page_embeds_catalog(player):
    """The wizard seeds the catalog into Alpine state so the power picker can
    autocomplete from it."""
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert "disciplinePowers" in r.text          # Alpine state key
    assert "Bond Famulus" in r.text              # a catalog power is embedded
