"""Downtime · Hunting — spend a timeskip roll to hunt (migration 049).

Generic for now: it decrements the shared per-timeskip roll budget and logs a
'hunt' downtime action; the actual outcome is ST/bot-resolved.
"""
from web.db import (get_db, upsert_player, upsert_settings, create_character,
                    create_period, set_period_active, get_active_period,
                    hunt_downtime, list_downtime_actions, timeskip_rolls_remaining,
                    delete_character)

_DISCORD = "881000000000000042"


def _ensure_period(conn):
    if not get_active_period(conn):
        per = create_period(conn, "Hunt Night", "night", "full",
                            "2026-09-01T18:00:00Z", "2026-09-30T06:00:00Z", "system")
        set_period_active(conn, per["id"])
    return get_active_period(conn)["id"]


def test_hunt_consumes_a_roll_and_logs(_client):
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=3)
        upsert_player(conn, _DISCORD, "HuntPlayer")
        cid = create_character(conn, _DISCORD, "Hunt Probe", "gangrel")["id"]
        per = _ensure_period(conn)
        before = timeskip_rolls_remaining(conn, cid)["remaining"]
        res = hunt_downtime(conn, cid)
        assert res["ok"] and res["remaining"] == before - 1
        assert timeskip_rolls_remaining(conn, cid)["remaining"] == before - 1
        hunts = list_downtime_actions(conn, cid, per, "hunt")
        assert len(hunts) == 1 and hunts[0]["kind"] == "hunt"
        delete_character(conn, cid)
        conn.commit()


def test_hunt_blocked_when_budget_exhausted(_client):
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=1)
        upsert_player(conn, _DISCORD, "HuntPlayer")
        cid = create_character(conn, _DISCORD, "Hunt Probe 2", "gangrel")["id"]
        _ensure_period(conn)
        assert hunt_downtime(conn, cid)["ok"] is True          # uses the lone roll
        out = hunt_downtime(conn, cid)                          # none left
        assert out["ok"] is False and "roll" in out["error"].lower()
        delete_character(conn, cid)
        conn.commit()


def test_hunt_route_spends_a_roll(player):
    from web.db import approve_character
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=5)
        cid = create_character(conn, discord_id="111111111111111111",
                               name="QA Hunter", clan="gangrel")["id"]
        approve_character(conn, cid, reviewer_id="999999999999999999")
        per = _ensure_period(conn)
        before = timeskip_rolls_remaining(conn, cid)["remaining"]
        conn.commit()
    try:
        r = player.post(f"/characters/{cid}/downtime/hunt",
                        data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
        assert r.status_code == 200                            # HTMX partial, not a redirect
        assert "left this timeskip" in r.text
        with get_db() as conn:
            after = timeskip_rolls_remaining(conn, cid)["remaining"]
            hunts = list_downtime_actions(conn, cid, per, "hunt")
        assert after == before - 1 and len(hunts) == 1
    finally:
        with get_db() as conn:
            delete_character(conn, cid)
            conn.commit()
