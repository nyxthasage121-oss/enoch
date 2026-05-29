"""XP cap toggle (Staff QA, migration 027).

chronicle_settings.xp_cap_enabled gates claim-approval capping + retirement:
  on  (default) -> award up to the per-character cap, open the retirement
                   window on first hit (current behavior)
  off           -> award the full claim, never auto-trigger retirement
"""
import pytest


@pytest.fixture(autouse=True)
def _env(_client):
    from web.db import get_db, upsert_settings
    def _reset():
        with get_db() as conn:
            upsert_settings(conn, xp_cap_enabled=1, xp_cap_amount=350)
            conn.commit()
    _reset()           # start each test cap-on at the default amount
    yield
    _reset()           # restore so neither the toggle nor the amount leaks


def _char_near_cap(conn):
    """A character 10 XP below its cap. Returns (character_id, cap)."""
    from web.db import create_character, get_character
    ch = create_character(conn, discord_id="cap-test", name="Cap Probe", clan="brujah")
    cap = get_character(conn, ch["id"])["xp_cap"]
    conn.execute("UPDATE characters SET xp_total=? WHERE id=?", (cap - 10, ch["id"]))
    return ch["id"], cap


def _pending_claim(conn, cid, amount):
    from web.db import create_claim
    cl = create_claim(
        conn, character_id=cid, play_period_id=0,
        claimed_criteria=[{"criteria_id": 1, "label": "Test", "xp_value_at_submission": amount}],
        rp_links=[],
    )
    return cl["id"]


def _cleanup(conn, cid, clid):
    conn.execute("DELETE FROM xp_claims WHERE id=?", (clid,))
    conn.execute("DELETE FROM characters WHERE id=?", (cid,))
    conn.commit()


def test_cap_enabled_caps_award_and_opens_retirement():
    from web.db import get_db, approve_claim, get_character, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, xp_cap_enabled=1)
        cid, cap = _char_near_cap(conn)
        clid = _pending_claim(conn, cid, 50)
        conn.commit()
        try:
            approve_claim(conn, clid, "staff")
            c = get_character(conn, cid)
            assert c["xp_total"] == cap, "should cap at the limit (awarded 10 of 50)"
            assert c["retirement_eligible_at"] is not None, "hitting cap opens retirement"
        finally:
            _cleanup(conn, cid, clid)


def test_cap_disabled_awards_full_and_no_retirement():
    from web.db import get_db, approve_claim, get_character, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, xp_cap_enabled=0)
        cid, cap = _char_near_cap(conn)
        clid = _pending_claim(conn, cid, 50)
        conn.commit()
        try:
            approve_claim(conn, clid, "staff")
            c = get_character(conn, cid)
            assert c["xp_total"] == cap + 40, "no cap -> full 50 awarded (was cap-10)"
            assert c["retirement_eligible_at"] is None, "no cap -> no auto-retirement"
        finally:
            _cleanup(conn, cid, clid)


def test_cap_amount_is_configurable():
    """Enforcement uses the chronicle-wide xp_cap_amount, not a hardcoded 350,
    so a shared chronicle can set its own cap."""
    from web.db import get_db, create_character, approve_claim, get_character, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, xp_cap_enabled=1, xp_cap_amount=200)
        ch = create_character(conn, discord_id="cap-test", name="Cap Amount", clan="brujah")
        cid = ch["id"]
        conn.execute("UPDATE characters SET xp_total=190 WHERE id=?", (cid,))   # 10 under the 200 cap
        clid = _pending_claim(conn, cid, 50)
        conn.commit()
        try:
            approve_claim(conn, clid, "staff")
            c = get_character(conn, cid)
            assert c["xp_total"] == 200, "should cap at the chronicle amount (200), awarding 10 of 50"
            assert c["retirement_eligible_at"] is not None
        finally:
            _cleanup(conn, cid, clid)
