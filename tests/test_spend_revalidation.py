"""Tests for the revalidate_spend cost diff (Pattern 4).

Lifted from MCbN's validate_spend_request shape: the diff badge tells
staff "player submitted X / system says Y" before they approve.
"""
import pytest


@pytest.fixture(autouse=True)
def _ensure_migrations(_client):
    yield


def test_revalidate_match():
    from web.xp_rules import revalidate_spend
    # Attribute: cost = new × 5; 1→2 = 10 XP
    spend = {
        "category": "Attribute",
        "current_dots": 1, "new_dots": 2,
        "verified_cost": 10,
    }
    out = revalidate_spend(spend)
    assert out["valid"]
    assert out["correct_cost"] == 10
    assert out["stored_cost"] == 10
    assert out["matches"]
    assert "agrees" in out["message"].lower()


def test_revalidate_disagrees_when_stored_is_wrong():
    from web.xp_rules import revalidate_spend
    spend = {
        "category": "Attribute",
        "current_dots": 1, "new_dots": 2,
        "verified_cost": 7,  # bogus
    }
    out = revalidate_spend(spend)
    assert out["valid"]
    assert out["correct_cost"] == 10
    assert out["stored_cost"] == 7
    assert not out["matches"]
    assert "system now says 10" in out["message"].lower()
    assert "+3" in out["message"]


def test_revalidate_invalid_category():
    from web.xp_rules import revalidate_spend
    spend = {
        "category": "Not A Real Category",
        "current_dots": 1, "new_dots": 2,
        "verified_cost": 10,
    }
    out = revalidate_spend(spend)
    assert not out["valid"]
    assert not out["matches"]
    assert "rule lookup failed" in out["message"].lower()


def test_revalidate_handles_missing_verified_cost():
    from web.xp_rules import revalidate_spend
    spend = {
        "category": "Attribute",
        "current_dots": 1, "new_dots": 2,
        # verified_cost missing
    }
    out = revalidate_spend(spend)
    assert out["stored_cost"] == 0
    assert out["correct_cost"] == 10
    assert not out["matches"]


def test_staff_spends_page_renders_diff_badge_when_costs_drift(staff):
    """If verified_cost in the DB doesn't match what the live rules
    compute, the spends page must show the 'system says X' badge."""
    from web.db import (
        get_db, create_character, update_character, adjust_xp_manual,
        upsert_player,
    )
    discord_id = "reval-test"
    with get_db() as conn:
        # list_spends_history INNER JOINs on player_profiles, so the
        # character's discord_id must have a matching row.
        upsert_player(conn, discord_id, username="RevalTestPlayer")
        ch = create_character(conn, discord_id=discord_id,
                              name="Reval Char", clan="brujah")
        cid = ch["id"]
        update_character(conn, cid, is_approved=1, status="active")
        adjust_xp_manual(conn, cid, 50, "seed", staff_id="0")
        # Hand-insert a spend whose verified_cost intentionally disagrees
        # with the live rules (Attribute 1→2 should be 10 XP, not 4).
        conn.execute("""
            INSERT INTO spend_requests
              (character_id, category, trait_name, current_dots, new_dots,
               verified_cost, status, submitted_at)
            VALUES (?, 'Attribute', 'Strength', 1, 2, 4, 'pending', ?)
        """, (cid, "2026-05-29T00:00:00Z"))
        conn.commit()
    try:
        r = staff.get("/staff/spends?status=pending")
        assert r.status_code == 200
        assert "system says 10 XP" in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM spend_requests WHERE character_id=?", (cid,))
            conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (cid,))
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            conn.execute("DELETE FROM player_profiles WHERE discord_id=?", (discord_id,))
            conn.commit()


def test_staff_spends_page_hides_diff_badge_when_costs_match(staff):
    """Sanity check: a spend whose stored cost matches the live recalc
    must NOT render the diff badge."""
    from web.db import (
        get_db, create_character, update_character, adjust_xp_manual,
        upsert_player,
    )
    discord_id = "ok-test"
    with get_db() as conn:
        upsert_player(conn, discord_id, username="OkTestPlayer")
        ch = create_character(conn, discord_id=discord_id,
                              name="Ok Char", clan="brujah")
        cid = ch["id"]
        update_character(conn, cid, is_approved=1, status="active")
        adjust_xp_manual(conn, cid, 50, "seed", staff_id="0")
        conn.execute("""
            INSERT INTO spend_requests
              (character_id, category, trait_name, current_dots, new_dots,
               verified_cost, status, submitted_at)
            VALUES (?, 'Attribute', 'Dexterity', 1, 2, 10, 'pending', ?)
        """, (cid, "2026-05-29T00:00:00Z"))
        conn.commit()
    try:
        r = staff.get("/staff/spends?status=pending")
        assert r.status_code == 200
        # Body must mention the character but no "system says" diff.
        assert "Ok Char" in r.text
        assert "system says" not in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM spend_requests WHERE character_id=?", (cid,))
            conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (cid,))
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            conn.execute("DELETE FROM player_profiles WHERE discord_id=?", (discord_id,))
            conn.commit()
