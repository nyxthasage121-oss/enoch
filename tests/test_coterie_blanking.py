"""Coterie shared-background blanking (migration 039).

A coterie's donated backgrounds form a shared pool any member can blank for the
night; blanked dots are unavailable to the WHOLE coterie until the next play
period — the same period-keyed release as the per-character feature, but scoped
to the coterie and derived live from active 'donated' contributions.
"""
import pytest

from web.db import (
    get_db, create_coterie, add_coterie_contribution,
    create_period, set_period_active, get_active_period,
    list_coterie_shared_backgrounds, blank_coterie_background,
    release_due_coterie_background_blanks,
)


@pytest.fixture(autouse=True)
def _migrated(_client):
    """Ensure the app lifespan (run_migrations + dev seed) has executed so the
    schema exists; these tests then talk to the DB directly."""
    return _client


def _make_period(conn, label, *, active=False):
    p = create_period(conn, label, "night", "full",
                      "2026-03-01T18:00:00Z", "2026-03-03T06:00:00Z", "system")
    if active:
        set_period_active(conn, p["id"])
    return p


def _donate_bg(conn, coterie_id, name, dots):
    add_coterie_contribution(
        conn, coterie_id=coterie_id, contribution_type="donated",
        target_kind="background", target_name=name, dots=dots,
        note="donation", recompute=False)


def _bg(conn, coterie_id, name):
    return next((b for b in list_coterie_shared_backgrounds(conn, coterie_id)
                 if b["name"] == name), None)


def test_shared_pool_sums_donations_per_background():
    with get_db() as conn:
        co = create_coterie(conn, "PoolCoterie")["id"]
        _donate_bg(conn, co, "Resources", 3)
        _donate_bg(conn, co, "Resources", 2)   # two donors, one background
        _donate_bg(conn, co, "Allies", 1)
        pool = {b["name"]: b for b in list_coterie_shared_backgrounds(conn, co)}
        assert pool["Resources"]["dots"] == 5        # summed across donors
        assert pool["Resources"]["available"] == 5
        assert pool["Resources"]["is_blanked"] is False
        assert pool["Allies"]["dots"] == 1


def test_blank_requires_active_period():
    with get_db() as conn:
        co = create_coterie(conn, "NoNightCoterie")["id"]
        _donate_bg(conn, co, "Resources", 3)
        prev = get_active_period(conn)
        conn.execute("UPDATE play_periods SET is_active=0")
        try:
            with pytest.raises(ValueError):
                blank_coterie_background(conn, co, "Resources", 1)
        finally:
            if prev:
                conn.execute("UPDATE play_periods SET is_active=1 WHERE id=?",
                             (prev["id"],))


def test_blank_validates_availability():
    with get_db() as conn:
        co = create_coterie(conn, "AvailCoterie")["id"]
        _make_period(conn, "Night CA1", active=True)
        _donate_bg(conn, co, "Resources", 3)
        with pytest.raises(ValueError):
            blank_coterie_background(conn, co, "Resources", 4)   # over the pool
        with pytest.raises(ValueError):
            blank_coterie_background(conn, co, "Ghouls", 1)      # not a shared bg


def test_blank_and_release_cycle():
    with get_db() as conn:
        co = create_coterie(conn, "CycleCoterie")["id"]
        _make_period(conn, "Night CB1", active=True)
        _donate_bg(conn, co, "Resources", 3)
        res = blank_coterie_background(conn, co, "Resources", 2)
        assert res["blanked_dots"] == 2 and res["available"] == 1
        assert res["period_label"] == "Night CB1"
        assert _bg(conn, co, "Resources")["available"] == 1
        # Not due while the blanking period is still active (other coteries'
        # blanks may release, but ours stays put).
        release_due_coterie_background_blanks(conn)
        assert _bg(conn, co, "Resources")["available"] == 1
        # Opening the next night releases the whole pool back to the coterie.
        _make_period(conn, "Night CB2", active=True)
        bg = _bg(conn, co, "Resources")
        assert bg["blanked_dots"] == 0 and bg["available"] == 3


def test_reblank_next_period_does_not_compound():
    with get_db() as conn:
        co = create_coterie(conn, "CompoundCoterie")["id"]
        _make_period(conn, "Night CC1", active=True)
        _donate_bg(conn, co, "Resources", 3)
        blank_coterie_background(conn, co, "Resources", 1)
        # Flip active to P2 WITHOUT auto-release to exercise the stale-blank path
        # inside blank_coterie_background itself.
        p2 = create_period(conn, "Night CC2", "night", "full",
                           "2026-03-05T18:00:00Z", "2026-03-07T06:00:00Z", "system")
        conn.execute("UPDATE play_periods SET is_active=0")
        conn.execute("UPDATE play_periods SET is_active=1 WHERE id=?", (p2["id"],))
        res = blank_coterie_background(conn, co, "Resources", 1)
        assert res["blanked_dots"] == 1   # the CC1 blank dropped, not stacked
        assert res["available"] == 2


def test_same_period_reblank_accumulates():
    with get_db() as conn:
        co = create_coterie(conn, "AccumCoterie")["id"]
        _make_period(conn, "Night CD1", active=True)
        _donate_bg(conn, co, "Resources", 3)
        blank_coterie_background(conn, co, "Resources", 1)
        res = blank_coterie_background(conn, co, "Resources", 1)
        assert res["blanked_dots"] == 2 and res["available"] == 1


# ── HTTP route: POST /coteries/{id}/backgrounds/blank ────────────────────────

def test_blank_route_member_can_blank(player):
    """A coterie member can blank a shared background via the route, and the
    pool drops for the whole coterie."""
    from web.db import (
        add_coterie_member, list_player_characters,
    )
    with get_db() as conn:
        # TestPlayer (discord 111…) owns Valeria Morano from the dev seed.
        valeria = next(c for c in list_player_characters(conn, "111111111111111111")
                       if c["name"] == "Valeria Morano")
        co = create_coterie(conn, "RouteBlankCoterie")["id"]
        add_coterie_member(conn, co, valeria["id"], role="member")
        _donate_bg(conn, co, "Resources", 3)
        _make_period(conn, "Route Night", active=True)

    r = player.post(f"/coteries/{co}/backgrounds/blank", data={
        "_csrf": "dev-csrf-token", "name": "Resources", "dots": "2",
    }, follow_redirects=False)
    assert r.status_code == 200

    with get_db() as conn:
        pool = _bg(conn, co, "Resources")
        assert pool["available"] == 1 and pool["blanked_dots"] == 2


def test_blank_route_rejects_non_member(player):
    """A player who owns no character in the coterie is refused (403)."""
    with get_db() as conn:
        co = create_coterie(conn, "NotMyCoterie")["id"]
        _donate_bg(conn, co, "Resources", 3)
        _make_period(conn, "Outsider Night", active=True)

    r = player.post(f"/coteries/{co}/backgrounds/blank", data={
        "_csrf": "dev-csrf-token", "name": "Resources", "dots": "1",
    }, follow_redirects=False)
    assert r.status_code == 403


def test_coterie_detail_renders_blank_and_funding_panels(player):
    """The detail page compiles and shows the new Shared Backgrounds (with a
    blank control) and Donations & Funding panels."""
    from web.db import add_coterie_member, list_player_characters
    with get_db() as conn:
        valeria = next(c for c in list_player_characters(conn, "111111111111111111")
                       if c["name"] == "Valeria Morano")
        co = create_coterie(conn, "RenderCoterie")["id"]
        add_coterie_member(conn, co, valeria["id"], role="member")
        _donate_bg(conn, co, "Resources", 3)
        _make_period(conn, "Render Night", active=True)

    r = player.get(f"/coteries/{co}")
    assert r.status_code == 200
    assert "Shared Backgrounds" in r.text
    assert "Blank for tonight" in r.text
    assert "Donations" in r.text and "Funding" in r.text
