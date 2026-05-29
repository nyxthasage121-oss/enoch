"""Tests for the chronicle_restrictions table (migration 022).

Covers the helpers and the wizard's predator-type gate that consumes them.
"""
import pytest


def _clear_restrictions():
    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM chronicle_restrictions")
        conn.commit()


@pytest.fixture(autouse=True)
def _isolated_restrictions(_client):
    """Each test starts with a clean restrictions table so state doesn't
    leak between tests in the same session. Depends on _client so the
    session-scoped TestClient (which runs migrations) is ready first."""
    _clear_restrictions()
    yield
    _clear_restrictions()


def test_default_allowed_component_is_allowed():
    from web.db import get_db, is_component_allowed
    with get_db() as conn:
        # Alleycat is default-allowed (not in V5_RESTRICTED_PREDATOR_TYPES).
        assert is_component_allowed(conn, "predator_type", "Alleycat",
                                    {"Blood Leech", "Tithe Collector"})


def test_default_restricted_component_is_blocked():
    from web.db import get_db, is_component_allowed
    with get_db() as conn:
        assert not is_component_allowed(conn, "predator_type", "Blood Leech",
                                        {"Blood Leech", "Tithe Collector"})


def test_unlock_default_restricted():
    from web.db import get_db, is_component_allowed, set_restriction
    with get_db() as conn:
        set_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                        reason="test", updated_by="0")
        conn.commit()
        assert is_component_allowed(conn, "predator_type", "Blood Leech",
                                    {"Blood Leech", "Tithe Collector"})


def test_ban_default_allowed():
    from web.db import get_db, is_component_allowed, set_restriction
    with get_db() as conn:
        set_restriction(conn, "predator_type", "Alleycat", "banned",
                        reason="test", updated_by="0")
        conn.commit()
        assert not is_component_allowed(conn, "predator_type", "Alleycat",
                                        {"Blood Leech", "Tithe Collector"})


def test_clear_restriction_reverts_to_default():
    from web.db import (
        get_db, is_component_allowed, set_restriction, clear_restriction,
    )
    with get_db() as conn:
        set_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                        updated_by="0")
        conn.commit()
        assert is_component_allowed(conn, "predator_type", "Blood Leech",
                                    {"Blood Leech"})
        clear_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                          actor_id="0")
        conn.commit()
        assert not is_component_allowed(conn, "predator_type", "Blood Leech",
                                        {"Blood Leech"})


def test_set_restriction_is_idempotent():
    from web.db import get_db, set_restriction, list_restrictions
    with get_db() as conn:
        set_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                        updated_by="0")
        set_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                        reason="updated", updated_by="0")
        conn.commit()
        rows = list_restrictions(conn, "predator_type")
    # Only one row, but with the updated reason.
    bl = [r for r in rows if r["component_id"] == "Blood Leech"]
    assert len(bl) == 1
    assert bl[0]["reason"] == "updated"


def test_wizard_available_predator_types_honors_unlock(player):
    """The chargen wizard hides restricted predator types from the
    selectable picker by default, and surfaces them once the chronicle
    unlocks them. Note: the predator_info JSON payload (Alpine reads it
    for the benefits popup) always includes every type — we only check
    that the <option> for the picker disappears/appears."""
    from web.db import get_db, set_restriction, clear_restriction

    # Default state — no selectable <option value="Blood Leech">.
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert '<option value="Blood Leech">Blood Leech</option>' not in r.text

    # Unlock — option must appear.
    with get_db() as conn:
        set_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                        reason="smoke test", updated_by="0")
        conn.commit()
    try:
        r = player.get("/characters/new")
        assert '<option value="Blood Leech">Blood Leech</option>' in r.text
    finally:
        with get_db() as conn:
            clear_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                              actor_id="0")
            conn.commit()


def test_admin_post_writes_restriction_row(staff):
    """Admin settings POST with `unlock_predator_Blood Leech=on` must
    create a chronicle_restrictions row (not the old JSON column)."""
    from web.db import get_db, get_restriction
    r = staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "standard",
            "revenant_families": "",
            "require_sheet_on_create": "on",
            "unlock_predator_Blood Leech": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        row = get_restriction(conn, "predator_type", "Blood Leech", "unlocked")
    assert row is not None
    assert row["mode"] == "unlocked"


def test_admin_post_unchecks_clears_restriction(staff):
    """Posting without the checkbox must delete an existing 'unlocked'
    row — the simplest way for staff to re-lock a predator type."""
    from web.db import get_db, set_restriction, get_restriction
    with get_db() as conn:
        set_restriction(conn, "predator_type", "Blood Leech", "unlocked",
                        reason="seed", updated_by="0")
        conn.commit()
    r = staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "standard",
            "revenant_families": "",
            "require_sheet_on_create": "on",
            # No unlock_predator_Blood Leech checkbox.
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        row = get_restriction(conn, "predator_type", "Blood Leech", "unlocked")
    assert row is None
