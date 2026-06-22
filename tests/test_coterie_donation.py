"""Donation form placement (2026-06-18): the donate-a-sheet-trait form is
surfaced inside the Coterie Creation block while the coterie is *forming*, and
in the Purchasing section once it's *active*. Both render the shared partial
`player/partials/coterie_donate_form.html`.
"""
import json

import pytest

from web.db import (
    get_db, create_coterie, add_coterie_member, list_player_characters,
    get_character,
)

_VALERIA_DISCORD = "111111111111111111"


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def _give_background(conn, char_id, name, dots):
    """Put a background on the character's sheet so they have something to
    donate (drives `viewer_donatable`)."""
    char = get_character(conn, char_id)
    sheet = dict(char.get("sheet_json") or {})
    bgs = [b for b in (sheet.get("backgrounds") or []) if b.get("name") != name]
    bgs.append({"name": name, "dots": dots})
    sheet["backgrounds"] = bgs
    conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                 (json.dumps(sheet), char_id))


def _valeria(conn):
    return next(c for c in list_player_characters(conn, _VALERIA_DISCORD)
                if c["name"] == "Valeria Morano")


def test_donate_form_in_creation_block_while_forming(player):
    with get_db() as conn:
        v = _valeria(conn)
        _give_background(conn, v["id"], "Allies", 2)
        co = create_coterie(conn, "DonateFormingCoterie", creation_state="forming")["id"]
        add_coterie_member(conn, co, v["id"], role="member")

    r = player.get(f"/coteries/{co}")
    assert r.status_code == 200
    assert "Donate a Trait from a Sheet" in r.text     # the Creation-block sub-section label
    assert "Donate to Coterie" in r.text               # the shared partial's submit button
    assert f"/coteries/{co}/donate" in r.text          # the form posts to the donate route
    assert "Allies" in r.text                          # the eligible trait is listed


def test_donate_form_in_purchasing_when_active(player):
    with get_db() as conn:
        v = _valeria(conn)
        _give_background(conn, v["id"], "Contacts", 1)
        co = create_coterie(conn, "DonateActiveCoterie", creation_state="active")["id"]
        add_coterie_member(conn, co, v["id"], role="member")

    r = player.get(f"/coteries/{co}")
    assert r.status_code == 200
    # In the active state the donate lives in the Purchasing collapsible.
    assert "Donate Trait from Sheet" in r.text         # the collapsible header
    assert "Donate to Coterie" in r.text               # the shared partial's submit button
    # The Creation-block sub-section only renders while forming.
    assert "Donate a Trait from a Sheet" not in r.text
