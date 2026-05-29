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
