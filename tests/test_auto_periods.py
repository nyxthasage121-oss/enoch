"""Tests for auto_create_next_period_if_due (migration 025).

Inference model:
  cadence  = gap between the two most recent periods' opens_at
  duration = the latest period's own open window
  next     = one cadence past the latest open, stamped when within
             `lead_days` of that opening.

Gated by chronicle_settings.auto_create_periods_enabled (off by default).
`now` is injected as an ISO string so the timing is deterministic without
touching the wall clock.
"""
import pytest


@pytest.fixture(autouse=True)
def _auto_period_env(_client):
    """Each test starts from a clean period table with the toggle OFF, and
    we reset both on teardown so the dashboard's lazy auto-create can never
    leak a stray period into unrelated tests."""
    from web.db import get_db

    def _reset():
        with get_db() as conn:
            conn.execute("DELETE FROM play_periods")
            conn.execute(
                "UPDATE chronicle_settings SET auto_create_periods_enabled=0 WHERE id=1"
            )
            conn.commit()

    _reset()
    yield
    _reset()


def _seed_two(conn):
    """Two baseline 'Night' periods 14 days apart, each a 2-day window."""
    from web.db import create_period
    create_period(conn, label="Night 1 — Full", period_type="night",
                  phase="full", opens_at="2026-01-01T20:00:00Z",
                  closes_at="2026-01-03T20:00:00Z", created_by="test")
    create_period(conn, label="Night 2 — Full", period_type="night",
                  phase="full", opens_at="2026-01-15T20:00:00Z",
                  closes_at="2026-01-17T20:00:00Z", created_by="test")
    conn.commit()


def _count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM play_periods").fetchone()["n"]


def test_disabled_is_noop():
    """Toggle off -> never stamps, even well past due."""
    from web.db import get_db, auto_create_next_period_if_due
    with get_db() as conn:
        _seed_two(conn)
        row = auto_create_next_period_if_due(conn, now="2026-02-01T00:00:00Z")
        assert row is None
        assert _count(conn) == 2


def test_needs_two_periods():
    """One period isn't enough history to infer a cadence."""
    from web.db import get_db, create_period, upsert_settings, auto_create_next_period_if_due
    with get_db() as conn:
        upsert_settings(conn, auto_create_periods_enabled=1)
        create_period(conn, label="Night 1 — Full", period_type="night",
                      phase="full", opens_at="2026-01-01T20:00:00Z",
                      closes_at="2026-01-03T20:00:00Z", created_by="test")
        conn.commit()
        row = auto_create_next_period_if_due(conn, now="2026-02-01T00:00:00Z")
        assert row is None
        assert _count(conn) == 1


def test_not_yet_due():
    """Within the cadence but before the lead window -> no-op."""
    from web.db import get_db, upsert_settings, auto_create_next_period_if_due
    with get_db() as conn:
        upsert_settings(conn, auto_create_periods_enabled=1)
        _seed_two(conn)
        # next opens 2026-01-29T20:00Z; with lead_days=2 it's due 2026-01-27T20:00Z
        row = auto_create_next_period_if_due(conn, now="2026-01-20T00:00:00Z")
        assert row is None
        assert _count(conn) == 2


def test_creates_when_due():
    """Within the lead window -> stamps Night N+1 with inferred window."""
    from web.db import get_db, upsert_settings, auto_create_next_period_if_due
    with get_db() as conn:
        upsert_settings(conn, auto_create_periods_enabled=1)
        _seed_two(conn)
        row = auto_create_next_period_if_due(conn, now="2026-01-28T00:00:00Z")
        assert row is not None
        assert row["label"] == "Night 3 — Full"
        assert row["opens_at"] == "2026-01-29T20:00:00Z"   # +14d cadence
        assert row["closes_at"] == "2026-01-31T20:00:00Z"  # +2d duration
        assert row["period_type"] == "night"
        assert row["phase"] == "full"
        assert row["is_active"] == 0   # never auto-opens
        assert _count(conn) == 3


def test_idempotent_after_create():
    """Re-running at the same instant doesn't double-stamp — the freshly
    created period becomes the anchor and its successor isn't due yet."""
    from web.db import get_db, upsert_settings, auto_create_next_period_if_due
    with get_db() as conn:
        upsert_settings(conn, auto_create_periods_enabled=1)
        _seed_two(conn)
        first = auto_create_next_period_if_due(conn, now="2026-01-28T00:00:00Z")
        assert first is not None
        again = auto_create_next_period_if_due(conn, now="2026-01-28T00:00:00Z")
        assert again is None
        assert _count(conn) == 3


def test_dormant_chronicle_rolls_forward():
    """If the chronicle went quiet, the slot rolls forward by whole cadences
    so we never stamp a period that's already closed at 'now'."""
    from web.db import get_db, upsert_settings, auto_create_next_period_if_due
    with get_db() as conn:
        upsert_settings(conn, auto_create_periods_enabled=1)
        _seed_two(conn)  # latest opens 2026-01-15, cadence 14d
        row = auto_create_next_period_if_due(conn, now="2026-04-10T00:00:00Z")
        assert row is not None
        # The created window must still be open (or future) at 'now',
        # never an already-closed period.
        assert row["closes_at"] > "2026-04-10T00:00:00Z"


def test_toggle_route_flips_flag(staff):
    """POST /staff/periods/auto-create-toggle persists the chronicle flag."""
    from web.db import get_db

    def _flag():
        with get_db() as conn:
            r = conn.execute(
                "SELECT auto_create_periods_enabled AS v FROM chronicle_settings WHERE id=1"
            ).fetchone()
            return r["v"] if r else None

    r = staff.post("/staff/periods/auto-create-toggle",
                   data={"_csrf": "dev-csrf-token", "enabled": "on"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert _flag() == 1

    # No 'enabled' field -> unchecked -> disabled.
    r = staff.post("/staff/periods/auto-create-toggle",
                   data={"_csrf": "dev-csrf-token"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert _flag() == 0
