"""Phase D — coterie projects (migration 041).

A project can be owned by a coterie instead of an individual: any member
proposes it (staff still approve), any member rolls it via the bot, and each
roll spends the ROLLING member's own per-character timeskip budget while
successes bank cumulatively on the shared stage list.
See docs/NYBN_DOWNTIME_PROJECTS.md — Phase D.
"""
import pytest

from web.db import (
    get_db, upsert_player, upsert_settings, create_character, create_period,
    set_period_active, get_active_period, create_coterie, add_coterie_member,
    create_project, approve_project, resolve_project_roll,
    list_projects_for_coterie, list_projects_for_character,
    timeskip_rolls_remaining, list_player_characters,
)

_BOT_HEADERS = {"Authorization": "Bearer smoke-test-token"}
_DA = "990000000000000001"   # member A's player
_DB = "990000000000000002"   # member B's player


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def _two_member_coterie(conn, name, *, cap=8):
    """An active coterie with two members owned by distinct players, plus an
    active play period. Returns (coterie_id, char_a, char_b)."""
    upsert_settings(conn, rolls_per_timeskip=cap)
    upsert_player(conn, _DA, "MemberAPlayer")
    upsert_player(conn, _DB, "MemberBPlayer")
    a = create_character(conn, _DA, name + " A", "tremere")
    b = create_character(conn, _DB, name + " B", "ventrue")
    co = create_coterie(conn, name + " Coterie")["id"]
    add_coterie_member(conn, co, a["id"], role="leader")
    add_coterie_member(conn, co, b["id"], role="member")
    if not get_active_period(conn):
        per = create_period(conn, "Night PD", "night", "full",
                            "2026-10-01T18:00:00Z", "2026-10-30T06:00:00Z", "system")
        set_period_active(conn, per["id"])
    return co, a, b


# ── DB engine ─────────────────────────────────────────────────────────────────

def test_coterie_project_owned_by_coterie_not_listed_as_individual():
    with get_db() as conn:
        co, a, b = _two_member_coterie(conn, "Listing")
        p = create_project(conn, a["id"], "Shared Elysium", "for the coterie",
                           proposed_by=_DA, coterie_id=co)
        assert p["is_coterie"] is True and p["coterie_id"] == co
        # Listed under the coterie...
        assert any(x["id"] == p["id"] for x in list_projects_for_coterie(conn, co))
        # ...and excluded from the proposer's individual project list.
        assert all(x["id"] != p["id"]
                   for x in list_projects_for_character(conn, a["id"]))


def test_rolls_charge_each_members_own_budget_and_bank_together():
    with get_db() as conn:
        co, a, b = _two_member_coterie(conn, "Budget")
        per = get_active_period(conn)["id"]
        pid = create_project(conn, a["id"], "Map the Rack", "",
                             proposed_by=_DA, coterie_id=co)["id"]
        approve_project(conn, pid, "staff", progress_type="roll",
                        payoff_type="freeform", roll_pool="3",
                        stages=[{"label": "Recon", "dc": 30}])
        # Member A rolls (charged to A); then member B rolls (charged to B).
        resolve_project_roll(conn, pid, successes=5, pool_size=6,
                             period_id=per, actor_character_id=a["id"])
        r = resolve_project_roll(conn, pid, successes=4, pool_size=6,
                                 period_id=per, actor_character_id=b["id"])
        # Successes bank cumulatively on the shared stage across both members.
        assert r["project"]["stages_json"][0]["progress"] == 9
        assert timeskip_rolls_remaining(conn, a["id"])["used"] == 1
        assert timeskip_rolls_remaining(conn, b["id"])["used"] == 1
        # The roll log attributes each roll to the member who made it.
        chars = {e.get("char") for e in r["project"]["log_json"] if e["kind"] == "roll"}
        assert chars == {a["name"], b["name"]}


def test_one_members_exhausted_budget_does_not_block_another():
    with get_db() as conn:
        co, a, b = _two_member_coterie(conn, "Isolation", cap=1)
        per = get_active_period(conn)["id"]
        pid = create_project(conn, a["id"], "Grind", "",
                             proposed_by=_DA, coterie_id=co)["id"]
        approve_project(conn, pid, "staff", progress_type="roll",
                        payoff_type="freeform", roll_pool="3", stages=[{"dc": 99}])
        resolve_project_roll(conn, pid, successes=1, pool_size=6,
                             period_id=per, actor_character_id=a["id"])
        # A has spent their only roll this timeskip...
        with pytest.raises(ValueError):
            resolve_project_roll(conn, pid, successes=1, pool_size=6,
                                 period_id=per, actor_character_id=a["id"])
        # ...but B still has their own budget and can keep the project moving.
        r = resolve_project_roll(conn, pid, successes=1, pool_size=6,
                                 period_id=per, actor_character_id=b["id"])
        assert r["project"]["stages_json"][0]["progress"] == 2
        upsert_settings(conn, rolls_per_timeskip=8)   # restore default for other tests


# ── Bot API ─────────────────────────────────────────────────────────────────

def test_bot_api_lists_coterie_project_for_member_and_rolls(_client):
    with get_db() as conn:
        co, a, b = _two_member_coterie(conn, "API")
        pid = create_project(conn, a["id"], "API Coterie Project", "",
                             proposed_by=_DA, coterie_id=co)["id"]
        approve_project(conn, pid, "staff", progress_type="roll",
                        payoff_type="freeform", roll_pool="3",
                        stages=[{"dc": 10}, {"dc": 10}])
        a_id, b_id = a["id"], b["id"]
    # The coterie project surfaces under member B's project list (B isn't the proposer).
    r = _client.get(f"/api/characters/{b_id}/projects", headers=_BOT_HEADERS)
    assert r.status_code == 200
    proj = next(p for p in r.json()["projects"] if p["id"] == pid)
    assert proj["is_coterie"] is True and proj["can_roll_now"] is True
    # B rolls it (charged to B), then A rolls it (charged to A); successes bank.
    r = _client.post(f"/api/projects/{pid}/roll", headers=_BOT_HEADERS,
                     json={"requester_discord_id": _DB, "successes": 4, "pool_size": 6})
    assert r.status_code == 200
    r = _client.post(f"/api/projects/{pid}/roll", headers=_BOT_HEADERS,
                     json={"requester_discord_id": _DA, "successes": 4, "pool_size": 6})
    assert r.status_code == 200
    assert r.json()["project"]["stages_json"][0]["progress"] == 8
    with get_db() as conn:
        assert timeskip_rolls_remaining(conn, a_id)["used"] == 1
        assert timeskip_rolls_remaining(conn, b_id)["used"] == 1


def test_bot_api_non_member_cannot_roll_coterie_project(_client):
    with get_db() as conn:
        co, a, b = _two_member_coterie(conn, "Guard")
        pid = create_project(conn, a["id"], "Guarded", "",
                             proposed_by=_DA, coterie_id=co)["id"]
        approve_project(conn, pid, "staff", progress_type="roll",
                        payoff_type="freeform", roll_pool="3", stages=[{"dc": 10}])
    r = _client.post(f"/api/projects/{pid}/roll", headers=_BOT_HEADERS,
                     json={"requester_discord_id": "000000000000000000",
                           "successes": 5, "pool_size": 6})
    assert r.status_code == 403


# ── Web route + detail render ─────────────────────────────────────────────────

def test_member_proposes_coterie_project_via_route(player):
    """A logged-in member proposes a coterie project from the coterie page; it
    lands in the coterie's pending queue and renders on the detail page."""
    with get_db() as conn:
        valeria = next(c for c in list_player_characters(conn, "111111111111111111")
                       if c["name"] == "Valeria Morano")
        co = create_coterie(conn, "ProposeCoterie")["id"]
        add_coterie_member(conn, co, valeria["id"], role="member")

    r = player.post(f"/coteries/{co}/projects/propose", data={
        "_csrf": "dev-csrf-token", "title": "Seize the docks",
        "description": "establish coterie domain",
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "Seize the docks" in r.text          # rendered in the Coterie Projects panel

    with get_db() as conn:
        projs = list_projects_for_coterie(conn, co)
        assert any(p["title"] == "Seize the docks" and p["status"] == "proposed"
                   for p in projs)


def test_propose_route_rejects_non_member(player):
    with get_db() as conn:
        co = create_coterie(conn, "NotMineProjectCoterie")["id"]
    r = player.post(f"/coteries/{co}/projects/propose", data={
        "_csrf": "dev-csrf-token", "title": "Trespass", "description": "",
    }, follow_redirects=False)
    assert r.status_code == 403


def test_staff_projects_page_renders_coterie_project(staff):
    """The staff queue renders a pending coterie project with its coterie label
    and the elevated coterie DC presets (30/45/60) in the stage builder — this
    guards the Jinja-in-Alpine preset injection in projects_tables.html."""
    with get_db() as conn:
        co, a, b = _two_member_coterie(conn, "StaffRender")
        create_project(conn, a["id"], "Coterie Haven", "shared domain",
                       proposed_by=_DA, coterie_id=co)
    r = staff.get("/staff/projects")
    assert r.status_code == 200
    assert "Coterie Haven" in r.text
    assert "StaffRender Coterie" in r.text          # coterie_name on the badge
    assert "30 / 45 / 60" in r.text                 # coterie DC preset hint


def test_coterie_detail_renders_projects_panel(player):
    """The detail page compiles and shows the Coterie Projects panel + propose form."""
    with get_db() as conn:
        valeria = next(c for c in list_player_characters(conn, "111111111111111111")
                       if c["name"] == "Valeria Morano")
        co = create_coterie(conn, "ProjectPanelCoterie")["id"]
        add_coterie_member(conn, co, valeria["id"], role="member")
    r = player.get(f"/coteries/{co}")
    assert r.status_code == 200
    assert "Coterie Projects" in r.text
    assert "Propose Coterie Project" in r.text
