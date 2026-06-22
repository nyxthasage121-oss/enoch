"""Chronicle-wide project mode toggle (migration 043). 'off' hides + blocks the
Projects feature everywhere; the helper normalises unknown values to 'nybn'."""
import pytest

from web.db import get_db, get_project_mode, projects_enabled, upsert_settings


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


@pytest.fixture
def _restore_mode():
    """Always put the chronicle back on NYbN so other suites' project tests pass."""
    yield
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", project_mode="nybn")


def test_default_and_helpers(_restore_mode):
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", project_mode="nybn")
        assert get_project_mode(conn) == "nybn" and projects_enabled(conn) is True
        upsert_settings(conn, actor_id="test", project_mode="off")
        assert get_project_mode(conn) == "off" and projects_enabled(conn) is False
        upsert_settings(conn, actor_id="test", project_mode="garbage")
        assert get_project_mode(conn) == "nybn"          # unknown -> default


def test_off_hides_player_projects_card(player, _restore_mode):
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", project_mode="nybn")
    assert "Propose Project" in player.get("/characters/1").text
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", project_mode="off")
    assert "Propose Project" not in player.get("/characters/1").text


def test_off_blocks_propose(player, _restore_mode):
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", project_mode="off")
    r = player.post("/characters/1/projects/propose",
                    data={"_csrf": "dev-csrf-token", "title": "Nope", "description": ""})
    assert r.status_code == 200 and "turned off" in r.text


def test_off_redirects_staff_projects(staff, _restore_mode):
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", project_mode="off")
    r = staff.get("/staff/projects", follow_redirects=False)
    assert r.status_code in (302, 303)
