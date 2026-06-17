"""Loresheets as a first-class field (packages/rules/loresheets.json), lifted
V5-generic from the friend's data set on 2026-06-17. Covers the catalog shape,
the trimmed wizard picker, the sheet parser (validation + canonicalization +
preservation), and the chargen embed."""
import json

from web.v5_traits import (
    LORESHEET_CATALOG, LORESHEET_PICKER, get_loresheet, LORESHEET_DOT_XP,
)


def test_catalog_loads():
    assert len(LORESHEET_CATALOG) == 149
    assert LORESHEET_DOT_XP == 3
    assert sum(len(l["dots"]) for l in LORESHEET_CATALOG) == 745


def test_catalog_shape_is_well_formed():
    ids = set()
    for l in LORESHEET_CATALOG:
        assert l["id"] and l["id"] not in ids
        ids.add(l["id"])
        assert l["name"] and isinstance(l["name"], str)
        assert isinstance(l["requires_st_permission"], bool)
        for d in l["dots"]:
            assert 1 <= d["dot"] <= 5
            assert d["name"] and isinstance(d["description"], str)


def test_picker_is_trimmed():
    by_id = {l["id"]: l for l in LORESHEET_PICKER}
    sample = LORESHEET_CATALOG[0]
    p = by_id[sample["id"]]
    assert p["name"] == sample["name"]
    assert len(p["dots"]) == len(sample["dots"])
    # No long descriptions embedded in the picker.
    for d in p["dots"]:
        assert set(d.keys()) <= {"dot", "name", "clan_restriction"}


def test_get_loresheet():
    sample = LORESHEET_CATALOG[0]
    assert get_loresheet(sample["id"])["name"] == sample["name"]
    assert get_loresheet("nonexistent-id") is None


def test_parse_validates_and_canonicalizes():
    from web.routes.player import _parse_sheet_from_form
    sample = LORESHEET_CATALOG[0]
    form = {"loresheets": json.dumps([
        {"id": sample["id"], "name": "WRONG NAME", "dots": 9},   # clamp + canonical
        {"id": "does-not-exist", "dots": 2},                     # dropped (unknown)
        {"id": sample["id"], "dots": 1},                         # dropped (duplicate)
    ])}
    ls = _parse_sheet_from_form(form)["loresheets"]
    assert len(ls) == 1
    assert ls[0]["id"] == sample["id"]
    assert ls[0]["name"] == sample["name"]      # canonicalized from catalog
    assert ls[0]["dots"] == 5                   # clamped to the sheet's max dot


def test_parse_preserves_loresheets_when_field_absent():
    from web.routes.player import _parse_sheet_from_form
    base = {"loresheets": [{"id": "x", "name": "X", "dots": 2}]}
    sheet = _parse_sheet_from_form({}, base=base)   # form omits 'loresheets'
    assert sheet["loresheets"] == base["loresheets"]


def test_chargen_page_embeds_picker(player):
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert "loresheetPicker" in r.text


def _make_char_with_loresheet(discord_id="111111111111111111"):
    import json as _json
    from web.db import get_db, upsert_player, create_character, get_character
    with get_db() as conn:
        upsert_player(conn, discord_id, "LSPlayer")
        cid = create_character(conn, discord_id, "Loresheet Subject", "tremere")["id"]
        sheet = (get_character(conn, cid).get("sheet_json") or {})
        sheet["loresheets"] = [{"id": "chamber-1444", "name": "1444 Chamber", "dots": 2}]
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (_json.dumps(sheet), cid))
    return cid


def test_player_view_renders_loresheet_panel(player):
    """Regression: the read-only loresheet panel renders (and the id→benefit
    lookup global is wired) on the player's own character page."""
    cid = _make_char_with_loresheet()
    r = player.get(f"/characters/{cid}")
    assert r.status_code == 200
    assert "1444 Chamber" in r.text
    assert "Shadow of the Chamber" in r.text       # dot-1 benefit name


def test_staff_view_renders_loresheet_panel(staff):
    """Regression: staff renders via its OWN Jinja env, so loresheets_by_id must
    be registered there too (a 500 otherwise)."""
    cid = _make_char_with_loresheet()
    r = staff.get(f"/staff/characters/{cid}")
    assert r.status_code == 200
    assert "1444 Chamber" in r.text
    assert "Shadow of the Chamber" in r.text
