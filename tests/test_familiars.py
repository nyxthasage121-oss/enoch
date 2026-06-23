"""Familiars (Animalism • Bond Famulus) — issue #1 follow-on.

The global animal catalog (7 V5 standards seeded by migration 048 + staff
customs) and per-character bonding.
"""


def test_standard_catalog_seeded(_client):
    from web.db import get_db, list_familiars
    with get_db() as conn:
        std = [f for f in list_familiars(conn) if f["is_standard"]]
    names = {f["name"] for f in std}
    assert {"Bat (Large)", "Bear", "Bird of Prey", "Guard Dog",
            "Horse", "Rat", "Wolf"} <= names
    wolf = next(f for f in std if f["name"] == "Wolf")
    assert wolf["physical"] == 6 and wolf["health"] == 6 and wolf["willpower"] == 3
    assert wolf["exceptional"].get("Stealth") == 5
    assert "wolf attacks" in (wolf["special"] or "")


def test_custom_familiar_crud(_client):
    from web.db import (get_db, create_familiar, update_familiar, delete_familiar,
                        get_familiar)
    with get_db() as conn:
        f = create_familiar(conn, name="QA Raven", physical=4, health=3, willpower=2,
                            exceptional={"Awareness": 6, "Stealth": 5},
                            special="Mimics speech.", created_by="test")
        fid = f["id"]
        assert f["is_standard"] is False and f["exceptional"]["Awareness"] == 6
        update_familiar(conn, fid, health=4, special="Mimics speech well.")
        assert get_familiar(conn, fid)["health"] == 4
        assert delete_familiar(conn, fid) is True
        assert get_familiar(conn, fid) is None
        conn.commit()


def test_standard_familiar_not_deletable(_client):
    from web.db import get_db, list_familiars, delete_familiar
    with get_db() as conn:
        wolf = next(f for f in list_familiars(conn) if f["name"] == "Wolf")
        assert delete_familiar(conn, wolf["id"]) is False
        assert any(f["name"] == "Wolf" for f in list_familiars(conn))


def test_bond_and_unbond_familiar(_client):
    from web.db import (get_db, upsert_player, create_character, list_familiars,
                        bond_familiar, list_character_familiars, unbond_familiar,
                        delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="773333773333773333", username="FamBond")
        ch = create_character(conn, discord_id="773333773333773333",
                              name="Familiar Probe", clan="nosferatu")
        cid = ch["id"]
        rat = next(f for f in list_familiars(conn) if f["name"] == "Rat")
    try:
        with get_db() as conn:
            bond = bond_familiar(conn, character_id=cid, familiar_id=rat["id"],
                                name="Whiskers", notes="messenger")
            assert bond["name"] == "Whiskers" and bond["animal"] == "Rat"
            assert bond["exceptional"]["Stealth"] == 7        # catalog stats merged in
            bonds = list_character_familiars(conn, cid)
            assert len(bonds) == 1 and bonds[0]["name"] == "Whiskers"
            unbond_familiar(conn, bond["id"])
            assert list_character_familiars(conn, cid) == []
            conn.commit()
    finally:
        with get_db() as conn:
            delete_character(conn, cid)
            conn.commit()


def test_familiars_page_loads(player):
    r = player.get("/characters/1/familiars")
    assert r.status_code == 200
    assert "Bestiary" in r.text and "Wolf" in r.text


def test_bond_gated_without_animalism(player):
    """A character without Animalism • can't bond a famulus."""
    from web.db import (get_db, create_character, list_familiars,
                        list_character_familiars, delete_character)
    with get_db() as conn:
        ch = create_character(conn, discord_id="111111111111111111",
                              name="QA NoAnimalism", clan="brujah",
                              sheet_json={"disc_potence": 2})
        cid = ch["id"]
        wolf = next(f for f in list_familiars(conn) if f["name"] == "Wolf")
        conn.commit()
    try:
        r = player.post(f"/characters/{cid}/familiars/bond", data={
            "_csrf": "dev-csrf-token", "familiar_id": str(wolf["id"]), "name": "NoGo",
        }, follow_redirects=False)
        assert r.status_code == 200 and "Animalism" in r.text
        with get_db() as conn:
            assert list_character_familiars(conn, cid) == []
    finally:
        with get_db() as conn:
            delete_character(conn, cid)
            conn.commit()


def test_bond_and_release_with_animalism(player):
    """With Animalism •, a character can bond and then release a famulus."""
    from web.db import (get_db, create_character, list_familiars,
                        list_character_familiars, delete_character)
    with get_db() as conn:
        ch = create_character(conn, discord_id="111111111111111111",
                              name="QA Animalist", clan="gangrel",
                              sheet_json={"disc_animalism": 1})
        cid = ch["id"]
        wolf = next(f for f in list_familiars(conn) if f["name"] == "Wolf")
        conn.commit()
    try:
        pg = player.get(f"/characters/{cid}/familiars")
        assert pg.status_code == 200 and "Bond a Famulus" in pg.text
        r = player.post(f"/characters/{cid}/familiars/bond", data={
            "_csrf": "dev-csrf-token", "familiar_id": str(wolf["id"]),
            "name": "Fang", "notes": "scout",
        }, follow_redirects=False)
        assert r.status_code == 303
        with get_db() as conn:
            bonds = list_character_familiars(conn, cid)
        assert len(bonds) == 1 and bonds[0]["name"] == "Fang" and bonds[0]["animal"] == "Wolf"
        r2 = player.post(f"/familiars/bonds/{bonds[0]['id']}/unbond",
                         data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
        assert r2.status_code == 303
        with get_db() as conn:
            assert list_character_familiars(conn, cid) == []
    finally:
        with get_db() as conn:
            delete_character(conn, cid)
            conn.commit()


def test_staff_sheet_shows_familiars(staff):
    from web.db import get_db, list_familiars, bond_familiar, unbond_familiar
    with get_db() as conn:
        wolf = next(f for f in list_familiars(conn) if f["name"] == "Wolf")
        bond = bond_familiar(conn, character_id=1, familiar_id=wolf["id"],
                             name="StaffSeesFang")
        conn.commit()
    try:
        r = staff.get("/staff/characters/1")
        assert r.status_code == 200 and "StaffSeesFang" in r.text
    finally:
        with get_db() as conn:
            unbond_familiar(conn, bond["id"])
            conn.commit()


def test_staff_bestiary_lists_catalog(staff):
    r = staff.get("/staff/familiars")
    assert r.status_code == 200
    assert "Bestiary" in r.text and "Wolf" in r.text and "Add a Custom Animal" in r.text


def test_staff_create_and_delete_custom(staff):
    from web.db import get_db, list_familiars
    r = staff.post("/staff/familiars", data={
        "_csrf": "dev-csrf-token", "name": "QA Catalog Raven", "description": "test",
        "physical": "4", "social": "1", "mental": "2", "health": "3", "willpower": "2",
        "exceptional": "Awareness 6, Stealth 5", "special": "Mimics speech.",
    }, follow_redirects=False)
    assert r.status_code == 303
    with get_db() as conn:
        raven = next((f for f in list_familiars(conn) if f["name"] == "QA Catalog Raven"), None)
    assert raven and raven["is_standard"] is False
    assert raven["exceptional"].get("Awareness") == 6 and raven["physical"] == 4
    r2 = staff.post(f"/staff/familiars/{raven['id']}/delete",
                    data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
    assert r2.status_code == 303
    with get_db() as conn:
        assert not any(f["name"] == "QA Catalog Raven" for f in list_familiars(conn))


def test_staff_cannot_delete_standard(staff):
    from web.db import get_db, list_familiars
    with get_db() as conn:
        wolf = next(f for f in list_familiars(conn) if f["name"] == "Wolf")
    r = staff.post(f"/staff/familiars/{wolf['id']}/delete",
                   data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
    assert r.status_code == 303                    # redirects, but delete is a no-op
    with get_db() as conn:
        assert any(f["name"] == "Wolf" for f in list_familiars(conn))


def test_familiars_cascade_on_character_delete(_client):
    from web.db import (get_db, upsert_player, create_character, list_familiars,
                        bond_familiar, list_character_familiars, delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="774444774444774444", username="FamCascade")
        ch = create_character(conn, discord_id="774444774444774444",
                              name="Fam Cascade Probe", clan="gangrel")
        cid = ch["id"]
        wolf = next(f for f in list_familiars(conn) if f["name"] == "Wolf")
        bond_familiar(conn, character_id=cid, familiar_id=wolf["id"], name="Fang")
        assert len(list_character_familiars(conn, cid)) == 1
        delete_character(conn, cid)
        assert list_character_familiars(conn, cid) == []
        conn.commit()
