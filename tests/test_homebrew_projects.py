"""Homebrew project engine (project_mode='homebrew', migration 050).

A single staff-set goal DC cumulative extended test, with an optional launch
roll and messy/bestial → pause-and-flag-ST (instead of NYbN's DC auto-bump).
"""
import pytest

from web.db import (get_db, upsert_settings, upsert_player, create_character,
                    create_period, set_period_active, get_active_period,
                    create_project, approve_project, resolve_homebrew_roll,
                    set_project_paused, get_project, count_active_alerts)

_DISCORD = "882000000000000077"


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def _setup(conn, *, launch_roll=False, target=10, launched=None):
    upsert_settings(conn, actor_id="t", project_mode="homebrew",
                    homebrew_launch_roll=(1 if launch_roll else 0),
                    rolls_per_timeskip=20)
    upsert_player(conn, _DISCORD, "HBPlayer")
    cid = create_character(conn, _DISCORD, "HB Probe", "tremere")["id"]
    if not get_active_period(conn):
        per = create_period(conn, "HB Night", "night", "full",
                            "2026-10-01T18:00:00Z", "2026-10-30T06:00:00Z", "system")
        set_period_active(conn, per["id"])
    pid = create_project(conn, cid, "Homebrew Probe", "", _DISCORD)["id"]
    _launched = launched if launched is not None else (0 if launch_roll else 1)
    approve_project(conn, pid, "staff", progress_type="roll", payoff_type="freeform",
                    roll_pool="resolve + occult", target_successes=target,
                    launched=_launched)
    return cid, pid


def _period(conn):
    return get_active_period(conn)["id"]


def test_no_launch_banks_progress_to_goal():
    with get_db() as conn:
        _cid, pid = _setup(conn, launch_roll=False, target=10)
        assert get_project(conn, pid)["launched"] is True       # opens directly
        r = resolve_homebrew_roll(conn, pid, successes=4, period_id=_period(conn))
        assert r["result"]["outcome"] == "progress" and r["result"]["progress"] == 4
        r2 = resolve_homebrew_roll(conn, pid, successes=7, period_id=_period(conn))
        assert r2["result"]["outcome"] == "goal_reached" and r2["result"]["progress"] == 11
        conn.commit()


def test_launch_roll_gates_then_opens():
    with get_db() as conn:
        _cid, pid = _setup(conn, launch_roll=True, target=8)
        assert get_project(conn, pid)["launched"] is False      # needs launching
        # A failed launch (no successes) keeps it closed.
        fail = resolve_homebrew_roll(conn, pid, successes=0, period_id=_period(conn))
        assert fail["result"]["outcome"] == "launch_failed"
        assert get_project(conn, pid)["launched"] is False
        # A success launches it — but doesn't bank test progress.
        ok = resolve_homebrew_roll(conn, pid, successes=3, period_id=_period(conn))
        assert ok["result"]["outcome"] == "launched"
        p = get_project(conn, pid)
        assert p["launched"] is True and p["progress_successes"] == 0
        # Now test rolls bank.
        t = resolve_homebrew_roll(conn, pid, successes=5, period_id=_period(conn))
        assert t["result"]["outcome"] == "progress" and t["result"]["progress"] == 5
        conn.commit()


def test_messy_crit_pauses_and_flags_st():
    with get_db() as conn:
        _cid, pid = _setup(conn, launch_roll=False, target=20)
        before = count_active_alerts(conn)
        r = resolve_homebrew_roll(conn, pid, successes=6, messy=True, period_id=_period(conn))
        assert r["result"]["outcome"] == "paused"
        p = get_project(conn, pid)
        assert p["paused"] is True and p["progress_successes"] == 6   # successes still bank
        assert count_active_alerts(conn) == before + 1               # ST flagged
        # No more rolls until staff clears the pause.
        with pytest.raises(ValueError):
            resolve_homebrew_roll(conn, pid, successes=3, period_id=_period(conn))
        conn.commit()


def test_bestial_failure_pauses():
    with get_db() as conn:
        _cid, pid = _setup(conn, launch_roll=False, target=20)
        # Hunger-die 1 with zero successes = bestial failure → pause.
        r = resolve_homebrew_roll(conn, pid, successes=0, hunger_one=True,
                                  period_id=_period(conn))
        assert r["result"]["outcome"] == "paused"
        assert get_project(conn, pid)["paused"] is True
        conn.commit()


def test_resume_clears_pause():
    with get_db() as conn:
        _cid, pid = _setup(conn, launch_roll=False, target=20)
        resolve_homebrew_roll(conn, pid, successes=2, messy=True, period_id=_period(conn))
        assert get_project(conn, pid)["paused"] is True
        set_project_paused(conn, pid, False, "staff")
        assert get_project(conn, pid)["paused"] is False
        # Rolling works again.
        r = resolve_homebrew_roll(conn, pid, successes=3, period_id=_period(conn))
        assert r["result"]["outcome"] == "progress"
        conn.commit()
