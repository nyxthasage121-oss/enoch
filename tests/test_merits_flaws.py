"""Merits & Flaws catalog (packages/rules/merits_flaws.json), lifted V5-generic
from the friend's data set on 2026-06-17. Validates the catalog shape, the
v5_traits loader, and that the wizard embeds it for autocomplete."""
from web.v5_traits import MERIT_CATALOG, FLAW_CATALOG, MERITS_FLAWS


def test_catalog_loads():
    assert len(MERIT_CATALOG) > 100
    assert len(FLAW_CATALOG) > 100
    assert MERITS_FLAWS["merits"] is MERIT_CATALOG
    assert MERITS_FLAWS["flaws"] is FLAW_CATALOG


def test_entry_shape_is_well_formed():
    for cat in (MERIT_CATALOG, FLAW_CATALOG):
        for e in cat:
            assert e["name"] and isinstance(e["name"], str)
            assert isinstance(e["costs"], list) and all(isinstance(c, int) for c in e["costs"])
            assert isinstance(e["summary"], str)
            assert isinstance(e["category"], str) and e["category"]
            assert isinstance(e["advanced"], bool)
            if "restriction" in e:
                assert e["restriction"] in {"caitiff", "ghoul", "thinblood"}


def test_no_duplicate_names_within_kind():
    for cat in (MERIT_CATALOG, FLAW_CATALOG):
        names = [e["name"].lower() for e in cat]
        assert len(names) == len(set(names))


def test_sorted_by_name():
    for cat in (MERIT_CATALOG, FLAW_CATALOG):
        names = [e["name"].lower() for e in cat]
        assert names == sorted(names)


def test_known_entries_present():
    assert "Beautiful" in {m["name"] for m in MERIT_CATALOG}
    assert "Baby Teeth" in {f["name"] for f in FLAW_CATALOG}


def test_chargen_page_embeds_catalogs(player):
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert "meritCatalog" in r.text and "flawCatalog" in r.text
    assert "Beautiful" in r.text                  # a catalog entry is embedded
