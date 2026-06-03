"""Tests for bulk XP award (web: paste → preview → atomic commit).

Gated on the `adjust_xp` permission (Storyteller+), so Helpers can't bulk-award.
All-or-nothing: any unresolved line holds the whole batch.
"""


def _make_active_char(name, clan="brujah", discord="bulk-pl"):
    """Create an approved/active character with a unique name and return its id."""
    from web.db import get_db, upsert_player, create_character, update_character
    with get_db() as conn:
        upsert_player(conn, discord_id=discord, username="BulkPlayer")
        ch = create_character(conn, discord_id=discord, name=name, clan=clan)
        update_character(conn, ch["id"], status="active")
    return ch["id"]


def test_resolve_bulk_xp_branches(_client):
    from web.db import get_db, resolve_bulk_xp
    _make_active_char("Zzz Resolve Alpha")
    text = "\n".join([
        "5 Zzz Resolve Alpha",     # ok
        "xx Zzz Resolve Alpha",    # non-numeric amount
        "0 Zzz Resolve Alpha",     # non-positive amount
        "3 Nobody Real Here",      # unknown character
        "2 Zzz Resolve Alpha",     # duplicate of the first
    ])
    with get_db() as conn:
        awards, errors = resolve_bulk_xp(conn, text)
    assert len(awards) == 1
    assert awards[0]["name"] == "Zzz Resolve Alpha"
    assert awards[0]["amount"] == 5
    assert len(errors) == 4


def test_bulk_xp_commit_awards_all(staff):
    from web.db import get_db, get_character
    cid = _make_active_char("Zzz Commit One")
    with get_db() as conn:
        before = get_character(conn, cid)["xp_total"]

    # Preview shows the resolved row without awarding.
    r = staff.post("/staff/xp/bulk", data={
        "_csrf": "dev-csrf-token", "reason": "Session 1",
        "entries": "7 Zzz Commit One", "confirm": "0"})
    assert r.status_code == 200
    assert "Zzz Commit One" in r.text
    with get_db() as conn:
        assert get_character(conn, cid)["xp_total"] == before  # preview ≠ commit

    # Confirm commits.
    r = staff.post("/staff/xp/bulk", data={
        "_csrf": "dev-csrf-token", "reason": "Session 1",
        "entries": "7 Zzz Commit One", "confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303
    with get_db() as conn:
        assert get_character(conn, cid)["xp_total"] == before + 7


def test_bulk_xp_all_or_nothing(staff):
    """A good line plus an unresolved line awards NOTHING, even with confirm=1."""
    from web.db import get_db, get_character
    cid = _make_active_char("Zzz Atomic One")
    with get_db() as conn:
        before = get_character(conn, cid)["xp_total"]
    r = staff.post("/staff/xp/bulk", data={
        "_csrf": "dev-csrf-token", "reason": "Session 1",
        "entries": "4 Zzz Atomic One\n3 Definitely Not A Character",
        "confirm": "1"})
    assert r.status_code == 200   # re-rendered with errors, not redirected
    with get_db() as conn:
        assert get_character(conn, cid)["xp_total"] == before  # unchanged


def test_bulk_xp_requires_reason(staff):
    from web.db import get_db, get_character
    cid = _make_active_char("Zzz Reason One")
    with get_db() as conn:
        before = get_character(conn, cid)["xp_total"]
    r = staff.post("/staff/xp/bulk", data={
        "_csrf": "dev-csrf-token", "reason": "",
        "entries": "5 Zzz Reason One", "confirm": "1"})
    assert r.status_code == 200
    with get_db() as conn:
        assert get_character(conn, cid)["xp_total"] == before


def test_bulk_xp_blocked_for_helper(staff):
    """A Helper (no adjust_xp) is 403'd by the route gate, which reads the role
    live from the DB — so even with a stale session they can't reach the tool."""
    from web.db import get_db, set_staff_role
    with get_db() as conn:
        set_staff_role(conn, "999999999999999999", "helper", actor_id="test")
    try:
        r = staff.get("/staff/xp/bulk", follow_redirects=False)
        assert r.status_code == 403
    finally:
        with get_db() as conn:
            set_staff_role(conn, "999999999999999999", "admin", actor_id="test")
