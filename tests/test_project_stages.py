"""Phase A — multi-stage project roll engine (migration 036).

The behaviour contract for `resolve_project_roll`: cumulative progress, stage
completion with overflow spill, and the crit / messy / bestial outcomes.
See docs/NYBN_DOWNTIME_PROJECTS.md — several behaviours rest on documented
assumptions; if a rule changes, change it here too.
"""
import pytest

from web.db import (
    get_db, upsert_player, upsert_settings, create_character, create_period,
    set_period_active, get_active_period,
    create_project, approve_project, resolve_project_roll, get_project,
)

_DISCORD = "880000000000000099"


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def _seed(conn, name, stages, *, cap=20):
    upsert_settings(conn, rolls_per_timeskip=cap)
    upsert_player(conn, _DISCORD, "StagePlayer")
    cid = create_character(conn, _DISCORD, name, "tremere")["id"]
    if not get_active_period(conn):
        per = create_period(conn, "Night Stage", "night", "full",
                            "2026-08-01T18:00:00Z", "2026-08-30T06:00:00Z", "system")
        set_period_active(conn, per["id"])
    pid = create_project(conn, cid, name + " project", "", _DISCORD)["id"]
    approve_project(conn, pid, "staff", progress_type="roll", payoff_type="freeform",
                    roll_pool="3", stages=stages)
    return pid


def _period_id(conn):
    return get_active_period(conn)["id"]


def test_stages_stored_on_approval():
    with get_db() as conn:
        pid = _seed(conn, "Stored", [{"label": "Recon", "dc": 15}, {"label": "Do", "dc": 30}])
        p = get_project(conn, pid)
        assert p["stage_count"] == 2
        assert p["stages_json"][0]["dc"] == 15 and p["stages_json"][0]["done"] is False
        assert p["current_stage"] == 0 and p["target_reached"] is False


def test_normal_completion_does_not_spill_overflow():
    with get_db() as conn:
        pid = _seed(conn, "NoSpill", [{"dc": 15}, {"dc": 30}])
        per = _period_id(conn)
        r1 = resolve_project_roll(conn, pid, successes=10, pool_size=8, period_id=per)
        assert r1["result"]["outcome"] == "progress" and r1["result"]["remaining"] == 5
        r2 = resolve_project_roll(conn, pid, successes=10, pool_size=8, period_id=per)
        assert r2["result"]["outcome"] == "stage_complete"
        assert r2["result"]["carry"] == 0            # plain success loses the leftover
        p = get_project(conn, pid)
        assert p["current_stage"] == 1
        assert p["stages_json"][0]["done"] is True
        assert p["stages_json"][1]["progress"] == 0


def test_messy_completion_carries_half():
    with get_db() as conn:
        pid = _seed(conn, "Messy", [{"dc": 10}, {"dc": 10}])
        r = resolve_project_roll(conn, pid, successes=16, messy=True, pool_size=8,
                                 period_id=_period_id(conn))
        assert "messy" in r["result"]["flags"] and r["result"]["carry"] == 3   # (16-10)//2
        assert get_project(conn, pid)["stages_json"][1]["progress"] == 3


def test_crit_completion_carries_full_and_flags():
    with get_db() as conn:
        pid = _seed(conn, "Crit", [{"dc": 10}, {"dc": 10}])
        r = resolve_project_roll(conn, pid, successes=16, critical=True, pool_size=8,
                                 period_id=_period_id(conn))
        assert "crit" in r["result"]["flags"] and r["result"]["carry"] == 6
        assert get_project(conn, pid)["stages_json"][1]["progress"] == 6


def test_bestial_banks_successes_then_bumps_dc():
    with get_db() as conn:
        pid = _seed(conn, "Bestial", [{"dc": 30}])
        # ceil(30/10)=3; 2 successes < 3 plus a Hunger 1 -> bestial
        r = resolve_project_roll(conn, pid, successes=2, hunger_one=True, pool_size=8,
                                 period_id=_period_id(conn))
        assert r["result"]["outcome"] == "bestial" and r["result"]["gained"] == 2
        p = get_project(conn, pid)
        assert p["stages_json"][0]["dc"] == 34       # 30 + 8//2
        assert p["stages_json"][0]["progress"] == 2  # the 2 successes still bank


def test_hunger_one_above_threshold_is_not_bestial():
    with get_db() as conn:
        pid = _seed(conn, "NotBestial", [{"dc": 30}])
        # 3 successes is NOT < ceil(30/10)=3 -> normal progress despite the Hunger 1
        r = resolve_project_roll(conn, pid, successes=3, hunger_one=True, pool_size=8,
                                 period_id=_period_id(conn))
        assert r["result"]["outcome"] == "progress"
        assert get_project(conn, pid)["stages_json"][0]["progress"] == 3


def test_final_stage_completion_marks_ready_and_flags_temp_dots_on_crit():
    with get_db() as conn:
        pid = _seed(conn, "Final", [{"dc": 10}])
        r = resolve_project_roll(conn, pid, successes=12, critical=True, pool_size=8,
                                 period_id=_period_id(conn))
        assert r["result"]["outcome"] == "project_complete"
        assert "final_temp_dots" in r["result"]["flags"]
        assert get_project(conn, pid)["target_reached"] is True


def test_budget_enforced_on_stage_rolls():
    with get_db() as conn:
        pid = _seed(conn, "StageBudget", [{"dc": 99}], cap=1)
        per = _period_id(conn)
        resolve_project_roll(conn, pid, successes=1, pool_size=8, period_id=per)
        with pytest.raises(ValueError):
            resolve_project_roll(conn, pid, successes=1, pool_size=8, period_id=per)
        upsert_settings(conn, rolls_per_timeskip=8)
