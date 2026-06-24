"""Roll macros — rich storage (legacy-tolerant), web CRUD, run-a-macro, card."""


def test_macro_db_helpers(_client):
    from web.db import (delete_character_macro, get_character, get_db, get_macro,
                        macros_from_sheet, save_character_macro)
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()
        saved = save_character_macro(conn, 1, "Frenzy", pool="wits + composure",
                                     difficulty=3, surge=True, comment="resist")
        conn.commit()
        assert saved["pool"] == "wits + composure"
        assert saved["difficulty"] == 3 and saved["surge"] is True
        sheet = get_character(conn, 1)["sheet_json"]
        assert get_macro(sheet, "Frenzy")["comment"] == "resist"
        assert len(macros_from_sheet(sheet)) == 1
        delete_character_macro(conn, 1, "Frenzy")
        conn.commit()
        assert macros_from_sheet(get_character(conn, 1)["sheet_json"]) == []


def test_legacy_string_macro_normalized():
    from web.db import get_macro, macros_from_sheet
    sheet = {"macros": {"Old": "strength + brawl"}}   # bot-style legacy macro
    m = get_macro(sheet, "Old")
    assert m["pool"] == "strength + brawl" and m["difficulty"] == 0 and m["surge"] is False
    assert macros_from_sheet(sheet)[0]["name"] == "Old"


def test_macro_web_crud(player):
    from web.db import get_db
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()
    r = player.post("/characters/1/macros", data={
        "_csrf": "dev-csrf-token", "name": "Brawl", "pool": "strength + brawl",
        "difficulty": "2", "surge": "on", "comment": "bar fight"})
    assert r.status_code == 200
    assert "Brawl" in r.text and "strength + brawl" in r.text
    r2 = player.post("/characters/1/macros/delete",
                     data={"_csrf": "dev-csrf-token", "name": "Brawl"})
    assert r2.status_code == 200
    assert "No macros yet" in r2.text


def test_run_macro_uses_its_pool(player):
    from web.db import get_db, save_character_macro
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        save_character_macro(conn, 1, "Five", pool="5", difficulty=2)
        conn.commit()
    r = player.post("/characters/1/roll", data={"_csrf": "dev-csrf-token", "macro": "Five"})
    assert r.status_code == 200
    assert "5d" in r.text       # the macro's pool (5)
    assert "vs 2" in r.text     # the macro's difficulty


def test_macro_card_on_character_page(player):
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", dice_roller_enabled=1)
        conn.commit()
    r = player.get("/characters/1")
    assert "Macros" in r.text and "Save Macro" in r.text
