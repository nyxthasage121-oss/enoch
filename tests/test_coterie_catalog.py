"""Coterie creation/purchase forms wire the V5 merit/background/flaw catalogs
for name autocomplete (the same catalogs the chargen wizard uses). Homebrew /
coterie-specific names stay free-text — the datalists only *suggest*.
"""
import pytest

from web.db import (
    get_db, create_coterie, add_coterie_member, list_player_characters,
)


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def test_forming_coterie_creation_forms_have_catalog_autocomplete(player):
    """A forming coterie shows the free-dots + flaw creation forms and the buy
    form, all wired to the catalog datalists."""
    with get_db() as conn:
        valeria = next(c for c in list_player_characters(conn, "111111111111111111")
                       if c["name"] == "Valeria Morano")
        co = create_coterie(conn, "AutocompleteCoterie", creation_state="forming")["id"]
        add_coterie_member(conn, co, valeria["id"], role="member")

    r = player.get(f"/coteries/{co}")
    assert r.status_code == 200
    # Datalists are rendered (and non-empty — real catalog options).
    assert 'id="coterie-merits"' in r.text
    assert 'id="coterie-backgrounds"' in r.text
    assert 'id="coterie-flaws"' in r.text
    assert r.text.count("<option value=") > 100        # ~79 + ~38 + ~110 catalog names
    # The merit/background inputs switch datalist by selected kind…
    assert "'coterie-backgrounds' : 'coterie-merits'" in r.text
    # …and the flaw input points at the flaw catalog.
    assert 'list="coterie-flaws"' in r.text
