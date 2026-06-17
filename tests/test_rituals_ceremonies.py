"""Blood Sorcery Rituals + Oblivion Ceremonies catalog
(packages/rules/rituals_ceremonies.json), lifted V5-generic from the friend's
data set on 2026-06-17. Validates the shape, loader, and wizard embedding."""
from web.v5_traits import RITUAL_CATALOG, CEREMONY_CATALOG


def test_catalogs_load():
    assert len(RITUAL_CATALOG) == 105
    assert len(CEREMONY_CATALOG) == 28


def test_entry_shape_is_well_formed():
    for cat in (RITUAL_CATALOG, CEREMONY_CATALOG):
        for e in cat:
            assert e["name"] and isinstance(e["name"], str)
            assert 1 <= e["level"] <= 5, (e["name"], e["level"])
            assert isinstance(e["summary"], str)
            assert isinstance(e["dice_pool"], str)
            assert isinstance(e["rouse_checks"], int) and e["rouse_checks"] >= 0


def test_sorted_by_level_then_name():
    for cat in (RITUAL_CATALOG, CEREMONY_CATALOG):
        keys = [(e["level"], e["name"].lower()) for e in cat]
        assert keys == sorted(keys)


def test_known_entries_present():
    assert "Astromancy" in {r["name"] for r in RITUAL_CATALOG}
    assert "Summon Spirit" in {c["name"] for c in CEREMONY_CATALOG}


def test_chargen_page_embeds_catalogs(player):
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert "ritualCatalog" in r.text and "ceremonyCatalog" in r.text
    assert "Astromancy" in r.text                 # a catalog ritual is embedded
