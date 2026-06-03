"""Tests for bot-driven staff role assignment.

Covers POST /api/staff/role (assign + revoke, Admin-gated) and GET
/api/staff/roster, plus the offline roster embed builder. The web layer is the
single authority: the issuing user's permission is checked against the live
matrix, so a non-Admin can't escalate via the bot.
"""
_BOT = {"Authorization": "Bearer smoke-test-token"}
_DEV_ADMIN = "999999999999999999"


def test_bot_admin_can_assign_and_revoke_role(_client):
    from web.db import get_db, upsert_player, get_staff_role, set_staff_role
    with get_db() as conn:
        upsert_player(conn, discord_id=_DEV_ADMIN, username="DevStaff")
        set_staff_role(conn, _DEV_ADMIN, "admin", actor_id="test")
        upsert_player(conn, discord_id="bot-target-1", username="BotTarget")

    r = _client.post("/api/staff/role", headers=_BOT, json={
        "actor_discord_id": _DEV_ADMIN,
        "target_discord_id": "bot-target-1",
        "target_username": "BotTarget",
        "role": "storyteller",
    })
    assert r.status_code == 200
    assert r.json()["role"] == "storyteller"
    with get_db() as conn:
        assert get_staff_role(conn, "bot-target-1") == "storyteller"

    # Revoke
    r = _client.post("/api/staff/role", headers=_BOT, json={
        "actor_discord_id": _DEV_ADMIN,
        "target_discord_id": "bot-target-1",
        "role": None,
    })
    assert r.status_code == 200
    with get_db() as conn:
        assert get_staff_role(conn, "bot-target-1") is None
        conn.execute("DELETE FROM player_profiles WHERE discord_id='bot-target-1'")


def test_bot_non_admin_cannot_assign_role(_client):
    """A Helper (no manage_roles) must not be able to assign roles via the bot."""
    from web.db import get_db, upsert_player, set_staff_role, get_staff_role
    with get_db() as conn:
        upsert_player(conn, discord_id="bot-helper", username="BotHelper")
        set_staff_role(conn, "bot-helper", "helper", actor_id="test")
        upsert_player(conn, discord_id="bot-victim", username="BotVictim")

    r = _client.post("/api/staff/role", headers=_BOT, json={
        "actor_discord_id": "bot-helper",
        "target_discord_id": "bot-victim",
        "role": "admin",
    })
    assert r.status_code == 403
    with get_db() as conn:
        assert get_staff_role(conn, "bot-victim") is None
        conn.execute(
            "DELETE FROM player_profiles WHERE discord_id IN ('bot-helper','bot-victim')")


def test_bot_role_endpoint_rejects_unknown_role(_client):
    from web.db import get_db, upsert_player, get_staff_role, set_staff_role
    with get_db() as conn:
        upsert_player(conn, discord_id=_DEV_ADMIN, username="DevStaff")
        set_staff_role(conn, _DEV_ADMIN, "admin", actor_id="test")
        upsert_player(conn, discord_id="bot-target-2", username="BotTarget2")
    r = _client.post("/api/staff/role", headers=_BOT, json={
        "actor_discord_id": _DEV_ADMIN,
        "target_discord_id": "bot-target-2",
        "role": "supreme_overlord",
    })
    assert r.status_code == 400
    with get_db() as conn:
        assert get_staff_role(conn, "bot-target-2") is None
        conn.execute("DELETE FROM player_profiles WHERE discord_id='bot-target-2'")


def test_bot_role_endpoint_requires_token(_client):
    _client.cookies.clear()
    r = _client.post("/api/staff/role", json={
        "actor_discord_id": _DEV_ADMIN, "target_discord_id": "x", "role": "helper"})
    assert r.status_code in (401, 403)


def test_bot_staff_roster_lists_assigned(_client):
    from web.db import get_db, upsert_player, set_staff_role
    with get_db() as conn:
        upsert_player(conn, discord_id="roster-st", username="RosterST")
        set_staff_role(conn, "roster-st", "storyteller", actor_id="test")
    r = _client.get("/api/staff/roster", headers=_BOT)
    assert r.status_code == 200
    staff = r.json()["staff"]
    assert any(m["discord_id"] == "roster-st" and m["role"] == "storyteller"
               for m in staff)
    with get_db() as conn:
        set_staff_role(conn, "roster-st", None, actor_id="test")
        conn.execute("DELETE FROM player_profiles WHERE discord_id='roster-st'")


def test_staff_roster_embed_groups_by_role():
    from bot.cogs.staff import build_roster_embed
    roster = [
        {"discord_id": "1", "username": "Alice", "role": "admin"},
        {"discord_id": "2", "username": "Bob", "role": "helper"},
    ]
    e = build_roster_embed(roster)
    names = [f.name for f in e.fields]
    assert "Admin" in names
    assert "Helper" in names
    # Empty roster → friendly description, no fields
    assert len(build_roster_embed([]).fields) == 0
