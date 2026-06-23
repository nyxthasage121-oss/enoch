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
