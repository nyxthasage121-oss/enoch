"""Staff live-vitals dashboard — GET /staff/vitals + the _vitals_row helper."""


def test_vitals_dashboard_renders_for_staff(staff):
    r = staff.get("/staff/vitals")
    assert r.status_code == 200
    assert "Vitals" in r.text
    assert "Valeria Morano" in r.text            # active + approved (char 1)
    for col in ("Hunger", "Health", "Willpower", "Humanity"):
        assert col in r.text


def test_vitals_dashboard_forbidden_for_players(player):
    assert player.get("/staff/vitals").status_code == 403


def test_vitals_dashboard_has_type_filter(staff):
    r = staff.get("/staff/vitals")
    assert r.status_code == 200
    assert "All types" in r.text and 'x-model="typeFilter"' in r.text


def test_vitals_row_computes_from_sheet():
    from web.routes.staff import _vitals_row
    c = {
        "id": 9, "name": "Probe", "clan": "brujah", "player_username": "p",
        "xp_available": 4,
        "sheet_json": {
            "hunger": 5, "attr_stamina": 2, "damage_health_sup": 5,
            "attr_composure": 2, "attr_resolve": 2, "humanity": 7,
        },
    }
    row = _vitals_row(c)
    assert row["hunger"] == 5
    assert row["health_marked"] == 5 and row["health_max"] == 5   # Stamina 2 + 3
    assert row["wp_max"] == 4                                     # Comp 2 + Res 2
    keys = {cond["key"] for cond in row["conditions"]}
    assert "hunger" in keys and "health" in keys                 # Ravenous + Impaired


# ── ST-tracker channel: post vitals to Discord (migration 055) ────────────────

def _set_st_channel(value):
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", st_channel_id=value)
        conn.commit()


def test_vitals_post_button_visibility(staff):
    _set_st_channel("123456789012345678")
    try:
        assert "Post to Discord" in staff.get("/staff/vitals").text
    finally:
        _set_st_channel("")
    assert "Post to Discord" not in staff.get("/staff/vitals").text


def test_vitals_post_enqueues_when_channel_set(staff):
    import json
    from web.db import get_db
    _set_st_channel("123456789012345678")
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM bot_outbox WHERE command='vitals_posted'")
            conn.commit()
        r = staff.post("/staff/vitals/post", data={"_csrf": "dev-csrf-token"})
        assert r.status_code in (200, 303)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT payload FROM bot_outbox WHERE command='vitals_posted'").fetchall()
        assert len(rows) == 1
        p = json.loads(rows[0]["payload"])
        assert p["channel_id"] == "123456789012345678"
        assert p["count"] >= 1
        assert isinstance(p["rows"], list) and p["rows"]
        assert "name" in p["rows"][0] and "hunger" in p["rows"][0]
    finally:
        _set_st_channel("")


def test_vitals_post_noop_without_channel(staff):
    from web.db import get_db
    _set_st_channel("")
    with get_db() as conn:
        conn.execute("DELETE FROM bot_outbox WHERE command='vitals_posted'")
        conn.commit()
    r = staff.post("/staff/vitals/post", data={"_csrf": "dev-csrf-token"})
    assert r.status_code in (200, 303)
    with get_db() as conn:
        rows = conn.execute("SELECT 1 FROM bot_outbox WHERE command='vitals_posted'").fetchall()
    assert rows == []


def test_vitals_post_forbidden_for_players(player):
    assert player.post("/staff/vitals/post",
                       data={"_csrf": "dev-csrf-token"}).status_code == 403
