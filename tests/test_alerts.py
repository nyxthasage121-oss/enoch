"""Operational alerts (migration 042): web 500s + bot-reported warn/errors
persist to a dismissable staff page."""
import pytest

from web.db import (
    get_db, log_alert, list_alerts, count_active_alerts,
    dismiss_alert, dismiss_all_alerts,
)

_BOT_HEADERS = {"Authorization": "Bearer smoke-test-token"}


@pytest.fixture(autouse=True)
def _migrated(_client):
    return _client


def _clear():
    with get_db() as conn:
        conn.execute("DELETE FROM app_alerts")
        conn.commit()


def test_log_list_count_dismiss():
    _clear()
    log_alert("web", "error", "unhandled", "Boom one", "tb...")
    log_alert("bot", "warn", "outbox", "Hiccup two")
    with get_db() as conn:
        assert count_active_alerts(conn) == 2
        active = list_alerts(conn)
        assert {a["source"] for a in active} == {"web", "bot"}
        aid = active[0]["id"]
        dismiss_alert(conn, aid, "staff1")
        assert count_active_alerts(conn) == 1
        assert aid not in [a["id"] for a in list_alerts(conn)]
        # ...but still visible in the "all" view.
        assert any(a["id"] == aid for a in list_alerts(conn, include_dismissed=True))
        assert dismiss_all_alerts(conn, "staff1") == 1
        assert count_active_alerts(conn) == 0


def test_bad_level_and_source_are_normalized():
    _clear()
    log_alert("nonsense", "loud", "x", "msg")
    with get_db() as conn:
        a = list_alerts(conn)[0]
        assert a["source"] == "web" and a["level"] == "error"


def test_bot_reports_alert_via_api(_client):
    _clear()
    r = _client.post("/api/alerts", headers=_BOT_HEADERS,
                     json={"level": "error", "event": "cmd",
                           "message": "Bot exploded", "detail": "tb"})
    assert r.status_code == 200 and r.json()["ok"] is True
    with get_db() as conn:
        assert any(a["source"] == "bot" and a["message"] == "Bot exploded"
                   for a in list_alerts(conn))


def test_alerts_api_requires_bot_token(_client):
    r = _client.post("/api/alerts", json={"message": "no token"})
    assert r.status_code in (401, 403)


def test_staff_alerts_page_renders_and_dismisses(staff):
    _clear()
    log_alert("web", "error", "unhandled", "Visible boom", "trace")
    r = staff.get("/staff/alerts")
    assert r.status_code == 200
    assert "Visible boom" in r.text
    with get_db() as conn:
        aid = list_alerts(conn)[0]["id"]
    r2 = staff.post(f"/staff/alerts/{aid}/dismiss",
                    data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
    assert r2.status_code in (302, 303)
    with get_db() as conn:
        assert count_active_alerts(conn) == 0
