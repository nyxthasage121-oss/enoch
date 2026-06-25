"""Staff character transfer — reassign a character to another player."""
import pytest


def test_transfer_character_helper_moves_owner_and_audits():
    from web.db import (get_character, get_db, get_player, transfer_character,
                        upsert_player)
    with get_db() as conn:
        upsert_player(conn, "555000111222", "NewOwner")     # registered target
        before = get_character(conn, 1)["discord_id"]
        char = transfer_character(conn, 1, "555000111222", actor_id="t")
        assert char["discord_id"] == "555000111222"
        # an existing username must NOT be clobbered by the id
        assert get_player(conn, "555000111222")["username"] == "NewOwner"
        assert conn.execute(
            "SELECT 1 FROM audit_log WHERE action='transfer_character' "
            "ORDER BY id DESC LIMIT 1").fetchall()
        # restore
        transfer_character(conn, 1, before, actor_id="t")
        conn.execute("DELETE FROM player_profiles WHERE discord_id='555000111222'")
        conn.commit()


def test_transfer_character_creates_unregistered_target():
    from web.db import get_character, get_db, get_player, transfer_character
    with get_db() as conn:
        before = get_character(conn, 1)["discord_id"]
        assert get_player(conn, "999777555333") is None
        transfer_character(conn, 1, "999777555333", actor_id="t")
        assert get_player(conn, "999777555333") is not None   # profile created
        transfer_character(conn, 1, before, actor_id="t")
        conn.execute("DELETE FROM player_profiles WHERE discord_id='999777555333'")
        conn.commit()


def test_transfer_rejects_bad_and_duplicate():
    from web.db import get_character, get_db, transfer_character
    with get_db() as conn:
        owner = get_character(conn, 1)["discord_id"]
        with pytest.raises(ValueError):
            transfer_character(conn, 1, "not-an-id", actor_id="t")
        with pytest.raises(ValueError):
            transfer_character(conn, 1, owner, actor_id="t")     # already owns it


def test_transfer_route_reassigns(staff):
    from web.db import get_character, get_db, transfer_character, upsert_player
    with get_db() as conn:
        upsert_player(conn, "666000111222", "RouteOwner")
        original = get_character(conn, 1)["discord_id"]
    try:
        r = staff.post("/staff/characters/1/transfer",
                       data={"_csrf": "dev-csrf-token", "new_discord_id": "666000111222"},
                       follow_redirects=False)
        assert r.status_code == 303
        with get_db() as conn:
            assert get_character(conn, 1)["discord_id"] == "666000111222"
    finally:
        with get_db() as conn:
            transfer_character(conn, 1, original, actor_id="t")
            conn.execute("DELETE FROM player_profiles WHERE discord_id='666000111222'")
            conn.commit()


def test_transfer_section_renders_on_detail(staff):
    r = staff.get("/staff/characters/1")
    assert r.status_code == 200
    assert "Transfer Character" in r.text
    assert "/staff/characters/1/transfer" in r.text
