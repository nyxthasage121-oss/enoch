"""Manual XP adjustment routing (Staff QA S1).

grant/remove move earned total (xp_total, the capped figure); refund/add-spend
move consumed spent (xp_spent). Available XP shifts the same either way, but
the earned/cap figure must only change for true grants/removes.
"""
import pytest


@pytest.fixture(autouse=True)
def _m(_client):
    yield


def _mk(conn, name):
    from web.db import create_character
    return create_character(conn, discord_id="mxp-test", name=name, clan="brujah")


def test_grant_and_remove_move_total():
    from web.db import get_db, adjust_xp_manual, get_character
    with get_db() as conn:
        ch = _mk(conn, "MXP Total")
        try:
            adjust_xp_manual(conn, ch["id"], 50, "grant", "staff", target="total")
            c = get_character(conn, ch["id"])
            assert (c["xp_total"], c["xp_spent"], c["xp_available"]) == (50, 0, 50)
            adjust_xp_manual(conn, ch["id"], -20, "remove", "staff", target="total")
            c = get_character(conn, ch["id"])
            assert (c["xp_total"], c["xp_available"]) == (30, 30)
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (ch["id"],))
            conn.commit()


def test_add_spend_and_refund_move_spent_not_total():
    from web.db import get_db, adjust_xp_manual, get_character
    with get_db() as conn:
        ch = _mk(conn, "MXP Spent")
        try:
            adjust_xp_manual(conn, ch["id"], 100, "grant", "staff", target="total")
            # add_spend 20 -> -20 available, spent +20, earned total untouched
            adjust_xp_manual(conn, ch["id"], -20, "Spend: x", "staff", target="spent")
            c = get_character(conn, ch["id"])
            assert c["xp_total"] == 100, "earned total must be untouched by add_spend"
            assert c["xp_spent"] == 20
            assert c["xp_available"] == 80
            # refund_spend 5 -> +5 available, spent -5
            adjust_xp_manual(conn, ch["id"], 5, "Refund: y", "staff", target="spent")
            c = get_character(conn, ch["id"])
            assert (c["xp_total"], c["xp_spent"], c["xp_available"]) == (100, 15, 85)
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (ch["id"],))
            conn.commit()


def test_add_spend_endpoint_routes_to_spent(staff):
    """End-to-end through the route's action->target mapping: the adjust-xp
    endpoint with action=add_spend moves xp_spent, not xp_total."""
    from web.db import get_db, adjust_xp_manual, get_character
    with get_db() as conn:
        ch = _mk(conn, "MXP Endpoint")
        adjust_xp_manual(conn, ch["id"], 100, "seed", "staff", target="total")
        conn.commit()
        cid = ch["id"]
    try:
        r = staff.post(
            f"/staff/characters/{cid}/adjust-xp",
            data={"_csrf": "dev-csrf-token", "action": "add_spend",
                  "amount": "20", "note": "off-system purchase"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            c = get_character(conn, cid)
        assert c["xp_total"] == 100, "add_spend must not touch earned total"
        assert c["xp_spent"] == 20
        assert c["xp_available"] == 80
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            conn.commit()
