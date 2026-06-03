"""Background-blanking engine tests (migration 033).

Per-character tracked backgrounds with a one-night blank/release cycle. Mirrors
the behaviour contract of the MCbN tracker's test_background_blanking, but here
"the next night" is a *different play period becoming active*, not an integer
night ordinal — so release is verified by activating a new period.
"""
import pytest

from web.db import (
    get_db, upsert_player, create_character, create_period, set_period_active,
    get_active_period,
    set_character_background, blank_character_background,
    list_character_backgrounds, release_due_background_blanks,
)

_DISCORD = "880000000000000007"


@pytest.fixture(autouse=True)
def _migrated(_client):
    """Ensure the app lifespan (run_migrations + dev seed) has executed so the
    schema exists; these tests then talk to the DB directly."""
    return _client


def _seed_character(conn, name):
    upsert_player(conn, _DISCORD, "BlankPlayer")
    return create_character(conn, _DISCORD, name, "ventrue")["id"]


def _make_period(conn, label, *, active=False):
    p = create_period(conn, label, "night", "full",
                      "2026-02-01T18:00:00Z", "2026-02-03T06:00:00Z", "system")
    if active:
        set_period_active(conn, p["id"])
    return p


def _bg(conn, char_id, name):
    return next((b for b in list_character_backgrounds(conn, char_id)
                 if b["name"] == name), None)


def test_set_creates_updates_and_removes():
    with get_db() as conn:
        cid = _seed_character(conn, "SetTester")
        set_character_background(conn, cid, "Resources", 3, "player:test")
        assert _bg(conn, cid, "Resources")["dots"] == 3
        set_character_background(conn, cid, "Resources", 5, "player:test")
        assert _bg(conn, cid, "Resources")["dots"] == 5
        res = set_character_background(conn, cid, "Resources", 0, "player:test")
        assert res["deleted"] is True
        assert _bg(conn, cid, "Resources") is None


def test_slug_dedupe_collapses_variants():
    with get_db() as conn:
        cid = _seed_character(conn, "SlugTester")
        set_character_background(conn, cid, "High Society", 2, "player:test")
        set_character_background(conn, cid, "high  society!", 4, "player:test")
        rows = [b for b in list_character_backgrounds(conn, cid)
                if b["name"].lower().startswith("high")]
        assert len(rows) == 1
        assert rows[0]["dots"] == 4  # second set updated the same row


def test_blank_requires_active_period():
    with get_db() as conn:
        cid = _seed_character(conn, "NoNightTester")
        set_character_background(conn, cid, "Allies", 3, "player:test")
        prev = get_active_period(conn)
        conn.execute("UPDATE play_periods SET is_active=0")
        try:
            with pytest.raises(ValueError):
                blank_character_background(conn, cid, "Allies", 1, "player:test")
        finally:
            if prev:
                conn.execute("UPDATE play_periods SET is_active=1 WHERE id=?",
                             (prev["id"],))


def test_blank_validates_availability():
    with get_db() as conn:
        cid = _seed_character(conn, "AvailTester")
        _make_period(conn, "Night A1", active=True)
        set_character_background(conn, cid, "Allies", 3, "player:test")
        with pytest.raises(ValueError):
            blank_character_background(conn, cid, "Allies", 4, "player:test")
        with pytest.raises(ValueError):
            blank_character_background(conn, cid, "Untracked", 1, "player:test")


def test_blank_and_release_cycle():
    with get_db() as conn:
        cid = _seed_character(conn, "CycleTester")
        _make_period(conn, "Night C1", active=True)
        set_character_background(conn, cid, "Allies", 3, "player:test")
        res = blank_character_background(conn, cid, "Allies", 2, "player:test")
        assert res["blanked_dots"] == 2
        assert res["available"] == 1
        assert res["period_label"] == "Night C1"
        # not due while the blanking period is still active
        assert release_due_background_blanks(conn) == []
        assert _bg(conn, cid, "Allies")["available"] == 1
        # opening the next night releases it (via set_period_active) and
        # enqueues exactly one background_released bot event
        before = conn.execute(
            "SELECT COUNT(*) c FROM bot_outbox WHERE command='background_released'"
        ).fetchone()["c"]
        _make_period(conn, "Night C2", active=True)
        after = conn.execute(
            "SELECT COUNT(*) c FROM bot_outbox WHERE command='background_released'"
        ).fetchone()["c"]
        assert after == before + 1
        bg = _bg(conn, cid, "Allies")
        assert bg["blanked_dots"] == 0
        assert bg["available"] == 3


def test_reblank_next_period_does_not_compound():
    with get_db() as conn:
        cid = _seed_character(conn, "CompoundTester")
        _make_period(conn, "Night D1", active=True)
        set_character_background(conn, cid, "Allies", 3, "player:test")
        blank_character_background(conn, cid, "Allies", 1, "player:test")
        # Flip active to P2 WITHOUT auto-release, to exercise the stale-blank
        # path inside blank_character_background itself.
        p2 = create_period(conn, "Night D2", "night", "full",
                           "2026-02-05T18:00:00Z", "2026-02-07T06:00:00Z", "system")
        conn.execute("UPDATE play_periods SET is_active=0")
        conn.execute("UPDATE play_periods SET is_active=1 WHERE id=?", (p2["id"],))
        res = blank_character_background(conn, cid, "Allies", 1, "player:test")
        assert res["blanked_dots"] == 1   # the D1 blank dropped, not stacked
        assert res["available"] == 2


def test_same_period_reblank_accumulates():
    with get_db() as conn:
        cid = _seed_character(conn, "AccumTester")
        _make_period(conn, "Night E1", active=True)
        set_character_background(conn, cid, "Herd", 3, "player:test")
        blank_character_background(conn, cid, "Herd", 1, "player:test")
        res = blank_character_background(conn, cid, "Herd", 1, "player:test")
        assert res["blanked_dots"] == 2
        assert res["available"] == 1


def test_lowering_total_reclamps_blank():
    with get_db() as conn:
        cid = _seed_character(conn, "ClampTester")
        _make_period(conn, "Night F1", active=True)
        set_character_background(conn, cid, "Contacts", 3, "player:test")
        blank_character_background(conn, cid, "Contacts", 3, "player:test")
        set_character_background(conn, cid, "Contacts", 1, "player:test")
        bg = _bg(conn, cid, "Contacts")
        assert bg["dots"] == 1
        assert bg["blanked_dots"] == 1
        assert bg["available"] == 0
