"""Tests for the post_wizard column (migration 026).

The short-form chargen "finished the wizard, still needs the full sheet"
flag moved out of the sheet_json blob (where it was an `_post_wizard`
sentinel) into a real characters.post_wizard column. These cover the
column's defaults + round-trip; the blob-strip half lives in
test_sheet_migrations.py.
"""
import pytest


@pytest.fixture(autouse=True)
def _migrations(_client):
    yield


def test_post_wizard_column_defaults_and_round_trips():
    from web.db import get_db, create_character, update_character, get_character
    with get_db() as conn:
        ch = create_character(conn, discord_id="pw-test", name="PW Test", clan="brujah")
        try:
            # Migration 026 added the column with DEFAULT 0.
            assert get_character(conn, ch["id"])["post_wizard"] == 0
            # update_character must accept it (it's in the ALLOWED set).
            update_character(conn, ch["id"], post_wizard=1)
            assert get_character(conn, ch["id"])["post_wizard"] == 1
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (ch["id"],))
            conn.commit()


def test_validation_rerender_preserves_sheet(player):
    """A chargen validation failure must NOT wipe the player's progress: the
    wizard re-renders with the sheet re-seeded into initialForm. Regression for
    the 'hit submit with no specialties, went back, lost everything' bug."""
    import re
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", active_ruleset="standard",
                        creation_mode="guided", require_sheet_on_create=1)
        conn.commit()
    # Incomplete-but-distinctive submission — fails the RAW attribute spread so
    # it reaches the re-render path, carrying values we can find echoed back.
    r = player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Rerender Probe", "clan": "brujah",
        "character_type": "kindred", "character_tier": "neonate",
        "attr_strength": "4", "attr_dexterity": "3", "attr_stamina": "2",
        "skill_spread": "balanced",
    }, follow_redirects=False)
    assert r.status_code == 200                          # re-rendered, not redirected
    # The sheet object is re-seeded into the wizard's initialForm (the fix);
    # before it, initialForm had no nested "sheet" and all progress was lost.
    # initialForm is tojson|forceescape'd, so the JSON quotes are HTML entities.
    assert re.search(r'(?:"|&#34;|&quot;)sheet(?:"|&#34;|&quot;)\s*:', r.text)
    assert "attr_strength" in r.text
