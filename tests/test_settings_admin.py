"""Tests for the settings_admin gate (Pattern 5, migration 024).

Three-tier resolution from web/deps.py::is_settings_admin:
  1. ENOCH_SETTINGS_ADMIN_IDS env var (comma-separated)
  2. player_profiles.settings_admin = 1
  3. Otherwise: 403
"""
import pytest


@pytest.fixture(autouse=True)
def _ensure_migrations(_client):
    yield


def test_dev_admin_has_settings_admin_flag(staff):
    """The dev seed creates DevStaff as 'admin' with the settings_admin flag
    set (migration 024 also backfilled it for any pre-existing top-tier role);
    check that DevStaff's profile carries the flag."""
    from web.db import get_db, get_player
    with get_db() as conn:
        prof = get_player(conn, "999999999999999999")
    assert prof is not None
    assert prof.get("settings_admin") == 1


def test_set_settings_admin_helper_audits():
    from web.db import get_db, set_settings_admin, get_player
    with get_db() as conn:
        from web.db import upsert_player
        upsert_player(conn, discord_id="sa-helper", username="HelperUser")
        prof_before = get_player(conn, "sa-helper")
        assert prof_before.get("settings_admin") == 0
        set_settings_admin(conn, "sa-helper", True, actor_id="0")
        conn.commit()
        prof_after = get_player(conn, "sa-helper")
        assert prof_after.get("settings_admin") == 1
        # Audit row written
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action='set_settings_admin' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchall()
        assert len(rows) == 1
        # Toggle off
        set_settings_admin(conn, "sa-helper", False, actor_id="0")
        conn.commit()
        assert get_player(conn, "sa-helper")["settings_admin"] == 0
        conn.execute("DELETE FROM player_profiles WHERE discord_id='sa-helper'")
        conn.commit()


def test_admin_settings_post_blocks_non_admin(staff, monkeypatch):
    """A staff session whose player_profile has settings_admin=0 must be
    blocked from POSTing chronicle settings — even with a staff_role.

    The gate now reads the flag live from the DB (no session cache), so a
    revoke takes effect on the very next request. This used to only be
    assertable on a 'fresh' path; now it's a hard 403 with the dev staff
    still logged in. The env override is cleared so tier 1 can't grant."""
    monkeypatch.delenv("ENOCH_SETTINGS_ADMIN_IDS", raising=False)
    from web.db import get_db, set_settings_admin
    # Revoke the dev staff's flag in the DB while their session stays live.
    with get_db() as conn:
        set_settings_admin(conn, "999999999999999999", False, actor_id="0")
        conn.commit()
    try:
        r = staff.post(
            "/staff/admin/settings",
            data={"_csrf": "dev-csrf-token", "active_ruleset": "standard",
                  "revenant_families": "", "require_sheet_on_create": "on"},
            follow_redirects=False,
        )
        assert r.status_code == 403
    finally:
        with get_db() as conn:
            set_settings_admin(conn, "999999999999999999", True, actor_id="0")
            conn.commit()


def test_env_override_grants_access(monkeypatch, _client):
    """ENOCH_SETTINGS_ADMIN_IDS env var must grant access even when the
    DB flag is off. Used for emergency bootstrap / locked-out scenarios."""
    from web.db import get_db, upsert_player
    # Create a non-staff user and log them in via the dev seed path.
    with get_db() as conn:
        upsert_player(conn, discord_id="env-override", username="EnvOverride")
        conn.commit()
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    # Manually inject a session as the env-override user.
    # The dev preview only has DevStaff + DevPlayer; we approximate by
    # asserting via the resolver helper directly.
    from web.deps import is_settings_admin
    monkeypatch.setenv("ENOCH_SETTINGS_ADMIN_IDS", "env-override,other-id")

    # Build a minimal request/user so we can call is_settings_admin directly.
    class _Req:
        session: dict = {}
    req = _Req()
    assert is_settings_admin(req, {"id": "env-override"})
    assert is_settings_admin(req, {"id": "other-id"})
    assert not is_settings_admin(req, {"id": "not-listed"})

    with get_db() as conn:
        conn.execute("DELETE FROM player_profiles WHERE discord_id='env-override'")
        conn.commit()


def test_set_settings_admin_route_flips_flag(staff):
    """The /staff/admin/settings-admin/{id}/set POST must persist the
    flag and audit it. Only existing admins can call it."""
    from web.db import get_db, get_player, upsert_player
    with get_db() as conn:
        upsert_player(conn, discord_id="grantee", username="Grantee")
        conn.commit()
        assert get_player(conn, "grantee")["settings_admin"] == 0
    try:
        # Grant
        r = staff.post(
            "/staff/admin/settings-admin/grantee/set",
            data={"_csrf": "dev-csrf-token", "enabled": "on"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            assert get_player(conn, "grantee")["settings_admin"] == 1
        # Revoke (no `enabled` checkbox)
        r = staff.post(
            "/staff/admin/settings-admin/grantee/set",
            data={"_csrf": "dev-csrf-token"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            assert get_player(conn, "grantee")["settings_admin"] == 0
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM player_profiles WHERE discord_id='grantee'")
            conn.commit()


def test_admin_html_renders_settings_admin_column(staff):
    """The Players tab must show the Settings Admin column header + a
    checkbox bound to each player's flag."""
    r = staff.get("/staff/admin")
    assert r.status_code == 200
    assert "Settings Admin" in r.text
    # The grant form action must be present.
    assert "/staff/admin/settings-admin/" in r.text


def test_staff_role_ids_roundtrip():
    """staff_role_ids (migration 056) serializes as JSON and reads back as a
    list of ints via get_staff_role_ids."""
    from web.db import get_db, get_settings, get_staff_role_ids, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", staff_role_ids=["111", "222"])
        conn.commit()
        assert get_staff_role_ids(conn) == [111, 222]
        assert isinstance(get_settings(conn).get("staff_role_ids"), list)
        upsert_settings(conn, actor_id="t", staff_role_ids=[])
        conn.commit()
        assert get_staff_role_ids(conn) == []


def test_staff_role_ids_robust_to_junk():
    from web.db import get_db, get_staff_role_ids, upsert_settings
    with get_db() as conn:
        conn.execute("UPDATE chronicle_settings SET staff_role_ids='not json' WHERE id=1")
        conn.commit()
        assert get_staff_role_ids(conn) == []
        upsert_settings(conn, actor_id="t", staff_role_ids=[])
        conn.commit()


def test_staff_roles_route_saves_and_digit_guards(staff):
    """The dedicated /staff/admin/staff-roles POST persists the selected role
    IDs (junk dropped). Settings-admin only."""
    from web.db import get_db, get_staff_role_ids, upsert_settings
    try:
        r = staff.post(
            "/staff/admin/staff-roles",
            data={"_csrf": "dev-csrf-token",
                  "staff_role_ids": ["555", "666", "notanid"]},
            follow_redirects=False)
        assert r.status_code == 303
        with get_db() as conn:
            assert get_staff_role_ids(conn) == [555, 666]
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="t", staff_role_ids=[])
            conn.commit()


def test_staff_roles_route_blocks_non_settings_admin(staff, monkeypatch):
    monkeypatch.delenv("ENOCH_SETTINGS_ADMIN_IDS", raising=False)
    from web.db import get_db, set_settings_admin
    with get_db() as conn:
        set_settings_admin(conn, "999999999999999999", False, actor_id="0")
        conn.commit()
    try:
        r = staff.post("/staff/admin/staff-roles",
                       data={"_csrf": "dev-csrf-token", "staff_role_ids": ["1"]},
                       follow_redirects=False)
        assert r.status_code == 403
    finally:
        with get_db() as conn:
            set_settings_admin(conn, "999999999999999999", True, actor_id="0")
            conn.commit()


def test_admin_discord_section_and_staff_access_fallback(staff):
    """Without a bot token (tests), the Discord & Irad channel fields fall back
    to manual ID inputs and Staff Access shows the env-config note."""
    r = staff.get("/staff/admin")
    assert r.status_code == 200
    assert "Discord &amp; Irad" in r.text
    assert 'id="dice_channel_id"' in r.text and 'id="st_channel_id"' in r.text
    assert "Staff Access" in r.text and "STAFF_ROLE_IDS" in r.text


def test_admin_pickers_render_with_bot_data(staff, monkeypatch):
    """When the bot can list channels/roles, the Discord & Irad fields render as
    dropdowns and Staff Access renders the role-checkbox picker. The route does a
    function-local import, so patching the source module attributes takes."""
    async def _chans():
        return [{"id": "111", "name": "dice-rolls", "category": "GAME"},
                {"id": "222", "name": "st-tracker", "category": "STAFF"}]

    async def _roles():
        return [{"id": "900", "name": "Storyteller"}, {"id": "901", "name": "Helper"}]

    monkeypatch.setattr("web.discord_api.guild_text_channels", _chans)
    monkeypatch.setattr("web.discord_api.guild_roles", _roles)
    r = staff.get("/staff/admin")
    assert r.status_code == 200
    assert '<select name="dice_channel_id"' in r.text          # channel dropdown
    assert "#dice-rolls" in r.text and "#st-tracker" in r.text
    assert "/staff/admin/staff-roles" in r.text                # role-picker form
    assert 'value="900"' in r.text and "Storyteller" in r.text


def test_admin_renders_enum_driven_options(staff):
    """The four admin dropdowns render from settings_enums (single source) —
    their labels/descriptions must still appear in the page."""
    r = staff.get("/staff/admin")
    assert r.status_code == 200
    assert "Guided creation" in r.text and "Open entry" in r.text       # creation_mode
    assert "Chronicle-tuned budgets" in r.text                          # ruleset desc
    assert "Add Empty — +1-in-6 Empty resonance" in r.text              # resonance_mode
    assert "NYbN — multi-stage extended test" in r.text                 # project_mode


def test_export_requires_settings_admin(staff, monkeypatch):
    """The full chronicle export (characters/claims/spends/ledger/audit log)
    is settings-admin only — a staff_role without the flag is blocked, since
    a read-only Helper shouldn't be able to dump the whole database."""
    monkeypatch.delenv("ENOCH_SETTINGS_ADMIN_IDS", raising=False)
    from web.db import get_db, set_settings_admin
    with get_db() as conn:
        set_settings_admin(conn, "999999999999999999", False, actor_id="0")
        conn.commit()
    try:
        r = staff.get("/staff/admin/export.json", follow_redirects=False)
        assert r.status_code == 403
    finally:
        with get_db() as conn:
            set_settings_admin(conn, "999999999999999999", True, actor_id="0")
            conn.commit()
    # With the flag restored, a settings-admin can export.
    ok = staff.get("/staff/admin/export.json")
    assert ok.status_code == 200
    assert "tables" in ok.json()
