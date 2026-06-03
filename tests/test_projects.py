"""Projects feature engine tests (migration 034).

Downtime endeavours: propose -> staff approve (pick staged/roll + freeform/
structured) -> work it down -> staff complete (applies payoff).
"""
import pytest

from web.db import (
    get_db, upsert_player, upsert_settings, create_character, create_period,
    set_period_active, get_character, get_active_period,
    create_project, get_project, approve_project, reject_project,
    add_project_note, record_project_roll, complete_project,
    list_pending_projects, timeskip_rolls_remaining,
)

_BOT_HEADERS = {"Authorization": "Bearer smoke-test-token"}

_DISCORD = "880000000000000042"


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def _seed_character(conn, name):
    upsert_player(conn, _DISCORD, "ProjPlayer")
    return create_character(conn, _DISCORD, name, "tremere")["id"]


def _count_event(conn, command):
    return conn.execute(
        "SELECT COUNT(*) c FROM bot_outbox WHERE command=?", (command,)
    ).fetchone()["c"]


def test_create_and_reject():
    with get_db() as conn:
        cid = _seed_character(conn, "ProjA")
        p = create_project(conn, cid, "Research the Sabbat", "digging into rivals", _DISCORD)
        assert p["status"] == "proposed"
        assert any(x["id"] == p["id"] for x in list_pending_projects(conn))
        before = _count_event(conn, "project_rejected")
        reject_project(conn, p["id"], "staff1", "Too broad — narrow it down.")
        assert _count_event(conn, "project_rejected") == before + 1
        assert get_project(conn, p["id"])["status"] == "rejected"


def test_approve_staged_note_and_complete_freeform():
    with get_db() as conn:
        cid = _seed_character(conn, "ProjB")
        p = create_project(conn, cid, "Build a haven", "", _DISCORD)
        approve_project(conn, p["id"], "staff1",
                        progress_type="staged", payoff_type="freeform")
        proj = get_project(conn, p["id"])
        assert proj["status"] == "active" and proj["progress_type"] == "staged"
        add_project_note(conn, p["id"], "staff1", "Found a promising warehouse.")
        assert any(e["kind"] == "note" for e in get_project(conn, p["id"])["log_json"])
        complete_project(conn, p["id"], "staff1", reward_text="Gains Haven 2.")
        proj = get_project(conn, p["id"])
        assert proj["status"] == "complete"
        assert proj["reward_text"] == "Gains Haven 2."


def test_approve_roll_requires_pool_and_target():
    with get_db() as conn:
        cid = _seed_character(conn, "ProjC")
        p = create_project(conn, cid, "Decode the grimoire", "", _DISCORD)
        with pytest.raises(ValueError):
            approve_project(conn, p["id"], "staff1", progress_type="roll",
                            payoff_type="freeform", roll_pool="", target_successes=0)


def test_roll_accumulates_within_budget_and_reaches_target():
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=8)
        cid = _seed_character(conn, "ProjD")
        per1 = create_period(conn, "Night P1", "night", "full",
                             "2026-03-01T18:00:00Z", "2026-03-30T06:00:00Z", "system")
        set_period_active(conn, per1["id"])
        p = create_project(conn, cid, "Decode the grimoire", "", _DISCORD)
        approve_project(conn, p["id"], "staff1", progress_type="roll",
                        payoff_type="freeform", roll_pool="resolve + occult",
                        target_successes=5)
        proj = record_project_roll(conn, p["id"], successes=3, outcome="success",
                                   period_id=per1["id"])
        assert proj["progress_successes"] == 3 and proj["target_reached"] is False
        # same timeskip — a second roll is allowed (rolls are a shared per-character
        # budget, not one-per-project) and crosses the target
        proj = record_project_roll(conn, p["id"], successes=3, outcome="critical",
                                   period_id=per1["id"])
        assert proj["progress_successes"] == 6 and proj["target_reached"] is True
        rem = timeskip_rolls_remaining(conn, cid)
        assert rem["used"] == 2 and rem["remaining"] == 6


def test_roll_budget_enforced_per_timeskip():
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=2)
        cid = _seed_character(conn, "ProjBudget")
        per = create_period(conn, "Night Budget", "night", "full",
                            "2026-06-01T18:00:00Z", "2026-06-30T06:00:00Z", "system")
        set_period_active(conn, per["id"])
        p = create_project(conn, cid, "Grind", "", _DISCORD)
        approve_project(conn, p["id"], "staff1", progress_type="roll",
                        payoff_type="freeform", roll_pool="3", target_successes=99)
        record_project_roll(conn, p["id"], successes=1, outcome="success", period_id=per["id"])
        record_project_roll(conn, p["id"], successes=1, outcome="success", period_id=per["id"])
        with pytest.raises(ValueError):  # budget of 2 is spent
            record_project_roll(conn, p["id"], successes=1, outcome="success",
                                period_id=per["id"])
        upsert_settings(conn, rolls_per_timeskip=8)  # restore default for other tests


def test_complete_structured_grants_dots_and_xp():
    with get_db() as conn:
        cid = _seed_character(conn, "ProjE")
        xp_before = get_character(conn, cid)["xp_total"]
        p = create_project(conn, cid, "Court a mentor", "", _DISCORD)
        approve_project(conn, p["id"], "staff1", progress_type="staged",
                        payoff_type="structured", reward_category="Advantage",
                        reward_trait="Mentor", reward_dots=2, reward_xp=5)
        before = _count_event(conn, "project_completed")
        complete_project(conn, p["id"], "staff1")
        assert _count_event(conn, "project_completed") == before + 1
        char = get_character(conn, cid)
        assert char["xp_total"] == xp_before + 5
        advs = (char.get("sheet_json") or {}).get("advantages") or []
        assert any(a.get("name") == "Mentor" and a.get("dots") == 2 for a in advs)
        proj = get_project(conn, p["id"])
        assert proj["status"] == "complete" and proj["payoff_applied"] == 1


# ── Route + bot-API smoke tests ───────────────────────────────────────────────

def test_propose_list_approve_flow(_client):
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    _client.get("/_dev/player", follow_redirects=False)
    # Player proposes on their approved seed character (id 1).
    r = _client.post("/characters/1/projects/propose",
                     data={"_csrf": "dev-csrf-token", "title": "Route Project",
                           "description": "smoke"})
    assert r.status_code == 200 and "Route Project" in r.text
    # Bot lists the character's projects and finds it pending.
    r = _client.get("/api/characters/1/projects", headers=_BOT_HEADERS)
    assert r.status_code == 200
    proj = next(p for p in r.json()["projects"] if p["title"] == "Route Project")
    assert proj["status"] == "proposed" and proj["can_roll_now"] is False
    pid = proj["id"]
    # Staff approve it (staged / freeform) and the page renders it active.
    _client.get("/_dev/seed", follow_redirects=False)  # become staff
    r = _client.post(f"/staff/projects/{pid}/approve",
                     data={"_csrf": "dev-csrf-token", "progress_type": "staged",
                           "payoff_type": "freeform"})
    assert r.status_code == 200
    r = _client.get("/staff/projects")
    assert r.status_code == 200 and "Route Project" in r.text
    r = _client.get("/api/characters/1/projects", headers=_BOT_HEADERS)
    assert next(p for p in r.json()["projects"] if p["id"] == pid)["status"] == "active"


def test_staff_dashboard_has_project_card(_client):
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    _client.get("/_dev/seed", follow_redirects=False)
    r = _client.get("/staff/")
    assert r.status_code == 200 and "Project Proposals" in r.text


def test_bot_roll_api_records_and_enforces_budget(_client):
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=1)
        if not get_active_period(conn):
            per = create_period(conn, "Night RouteRoll", "night", "full",
                                "2026-05-01T18:00:00Z", "2026-05-30T06:00:00Z", "system")
            set_period_active(conn, per["id"])
        owner = get_character(conn, 1)["discord_id"]
        proj = create_project(conn, 1, "API Roll Project", "", owner)
        approve_project(conn, proj["id"], "staff", progress_type="roll",
                        payoff_type="freeform", roll_pool="3", target_successes=99)
        pid = proj["id"]
    body = {"requester_discord_id": owner, "successes": 2, "outcome": "success"}
    r = _client.post(f"/api/projects/{pid}/roll", headers=_BOT_HEADERS, json=body)
    assert r.status_code == 200 and r.json()["project"]["progress_successes"] == 2
    # budget of 1 is now spent -> next roll 400
    r2 = _client.post(f"/api/projects/{pid}/roll", headers=_BOT_HEADERS,
                      json={"requester_discord_id": owner, "successes": 1, "outcome": "success"})
    assert r2.status_code == 400
    # a non-owner is rejected (ownership checked before the budget)
    r3 = _client.post(f"/api/projects/{pid}/roll", headers=_BOT_HEADERS,
                      json={"requester_discord_id": "000000000000000000",
                            "successes": 1, "outcome": "success"})
    assert r3.status_code == 403
    with get_db() as conn:
        upsert_settings(conn, rolls_per_timeskip=8)  # restore default
