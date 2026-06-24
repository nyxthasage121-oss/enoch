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
