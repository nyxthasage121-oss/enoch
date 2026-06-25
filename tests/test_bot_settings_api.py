"""Bot-driven chronicle settings — GET/POST /api/settings (Settings-Admin gated,
curated allowlist) + the announce-channel fallback + the offline /settings embed.
The web is the single authority: a non-admin can't change settings via the bot."""
import os

# bot/config.py reads these at import time; give safe defaults for the embed test.
os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS", "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "smoke-test-token")

_BOT = {"Authorization": "Bearer smoke-test-token"}
_DEV_ADMIN = "999999999999999999"   # DevStaff — carries the settings_admin flag


def _ensure_admin():
    from web.db import get_db, set_settings_admin, set_staff_role, upsert_player
    with get_db() as conn:
        upsert_player(conn, discord_id=_DEV_ADMIN, username="DevStaff")
        set_staff_role(conn, _DEV_ADMIN, "admin", actor_id="test")
        set_settings_admin(conn, _DEV_ADMIN, True, actor_id="test")
        conn.commit()


def test_get_settings_returns_curated_view(_client):
    _ensure_admin()
    r = _client.get("/api/settings", headers=_BOT)
    assert r.status_code == 200
    s = r.json()
    for k in ("dice_channel_id", "st_channel_id", "announce_channel_id",
              "dice_roller_enabled", "resonance_mode", "project_mode",
              "xp_cap_enabled", "xp_cap_amount", "max_chars_per_player"):
        assert k in s


def test_settings_requires_token(_client):
    _client.cookies.clear()
    assert _client.get("/api/settings").status_code in (401, 403)


def test_admin_can_update_channel_and_roller(_client):
    from web.db import get_db, get_settings, upsert_settings
    _ensure_admin()
    try:
        r = _client.post("/api/settings", headers=_BOT, json={
            "actor_discord_id": _DEV_ADMIN,
            "fields": {"dice_channel_id": "123456789012345678",
                       "dice_roller_enabled": False}})
        assert r.status_code == 200
        body = r.json()
        assert body["dice_channel_id"] == "123456789012345678"
        assert body["dice_roller_enabled"] == 0
        with get_db() as conn:
            assert get_settings(conn).get("dice_channel_id") == "123456789012345678"
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="t", dice_channel_id="", dice_roller_enabled=1)
            conn.commit()


def test_channel_digit_guard(_client):
    from web.db import get_db, upsert_settings
    _ensure_admin()
    try:
        r = _client.post("/api/settings", headers=_BOT, json={
            "actor_discord_id": _DEV_ADMIN, "fields": {"st_channel_id": "not-a-channel"}})
        assert r.status_code == 200
        assert r.json()["st_channel_id"] == ""
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="t", st_channel_id="")
            conn.commit()


def test_non_settings_admin_blocked(_client, monkeypatch):
    monkeypatch.delenv("ENOCH_SETTINGS_ADMIN_IDS", raising=False)
    from web.db import get_db, upsert_player
    with get_db() as conn:
        upsert_player(conn, discord_id="bot-nonadmin", username="NonAdmin")
        conn.commit()
    try:
        r = _client.post("/api/settings", headers=_BOT, json={
            "actor_discord_id": "bot-nonadmin", "fields": {"dice_roller_enabled": True}})
        assert r.status_code == 403
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM player_profiles WHERE discord_id='bot-nonadmin'")
            conn.commit()


def test_invalid_enum_and_unknown_key_rejected(_client):
    _ensure_admin()
    r1 = _client.post("/api/settings", headers=_BOT, json={
        "actor_discord_id": _DEV_ADMIN, "fields": {"resonance_mode": "bogus"}})
    assert r1.status_code == 400
    r2 = _client.post("/api/settings", headers=_BOT, json={
        "actor_discord_id": _DEV_ADMIN, "fields": {"active_ruleset": "homebrew"}})
    assert r2.status_code == 400          # not in the bot's allowlist


def test_announce_channel_db_then_env_fallback(_client, monkeypatch):
    from web.db import get_announce_channel_id, get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", announce_channel_id="")
        conn.commit()
    monkeypatch.setattr("web.db.settings.CHRONICLE_CHANNEL_ID", 555000111, raising=False)
    try:
        with get_db() as conn:
            assert get_announce_channel_id(conn) == "555000111"        # DB blank → env
            upsert_settings(conn, actor_id="t", announce_channel_id="777")
            conn.commit()
            assert get_announce_channel_id(conn) == "777"              # DB wins
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="t", announce_channel_id="")
            conn.commit()


def test_get_settings_editable_flag(_client):
    _ensure_admin()
    assert _client.get("/api/settings", headers=_BOT,
                       params={"actor": _DEV_ADMIN}).json()["editable"] is True
    assert _client.get("/api/settings", headers=_BOT,
                       params={"actor": "nobody-xyz"}).json()["editable"] is False
    assert _client.get("/api/settings", headers=_BOT).json().get("editable") is False


# ── /settings interactive menu (Components V2, Inconnu-style) ─────────────────

_VIEW_DATA = {
    "dice_channel_id": "111", "st_channel_id": "", "announce_channel_id": "222",
    "dice_roller_enabled": 1, "resonance_mode": "tattered_facade", "project_mode": "off",
    "xp_cap_enabled": 0, "xp_cap_amount": 350, "max_chars_per_player": 2,
}


def test_settings_view_constructs_and_reflects_state():
    import discord
    from bot.cogs.settings import SettingsView
    v = SettingsView({**_VIEW_DATA, "editable": True}, actor_id="1", guild=None)
    kids = list(v.walk_children())
    selects = [x for x in kids if isinstance(x, (discord.ui.Select, discord.ui.ChannelSelect))]
    buttons = [x for x in kids if isinstance(x, discord.ui.Button)]
    assert len(buttons) == 2                         # roller + xp-cap toggles
    assert len(selects) == 7                         # 2 enum + 2 number + 3 channel
    # the resonance dropdown marks the current value as its default option
    res = next(x for x in selects if isinstance(x, discord.ui.Select)
               and "Resonance" in (x.placeholder or ""))
    assert any(o.value == "tattered_facade" and o.default for o in res.options)


def test_settings_view_disabled_when_not_editable():
    import discord
    from bot.cogs.settings import SettingsView
    v = SettingsView({**_VIEW_DATA, "editable": False}, actor_id="1", guild=None)
    interactive = [x for x in v.walk_children()
                   if isinstance(x, (discord.ui.Select, discord.ui.ChannelSelect, discord.ui.Button))]
    assert interactive and all(x.disabled for x in interactive)
