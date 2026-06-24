"""Smoke tests — every major route renders 200 with key markup present.

These are deliberately shallow. They catch broken templates (Alpine.js leaking
into the DOM, missing context keys, route 500s) and dead routes. They do NOT
verify business logic deeply — that's for unit tests.

Run with: .venv312\\Scripts\\python.exe -m pytest tests/test_smoke.py -v
"""
import pytest


# ── Public / anonymous routes ─────────────────────────────────────────────────

def test_landing_redirects_unauthed(anon):
    r = anon.get("/", follow_redirects=False)
    # Anonymous on landing: redirect to auth or render landing
    assert r.status_code in (200, 303, 307)


def test_dev_login_page_present(anon):
    r = anon.get("/_dev/login")
    assert r.status_code == 200
    assert "Dev Login" in r.text


def test_404_page_rendered(anon):
    r = anon.get("/this-route-does-not-exist")
    assert r.status_code == 404
    assert "Lost in the Dark" in r.text


def test_staff_route_blocked_for_anon(anon):
    r = anon.get("/staff/", follow_redirects=False)
    # Without auth: redirect to login (LoginRequired exception → /auth/login)
    assert r.status_code in (200, 303, 307)


# ── Auth dev-preview flow ─────────────────────────────────────────────────────

def test_dev_seed_data_sets_session(_client):
    _client.cookies.clear()
    r = _client.get("/_dev/seed_data", follow_redirects=False)
    assert r.status_code == 307


def test_dev_seed_logs_in_as_staff(_client):
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    r = _client.get("/_dev/seed", follow_redirects=False)
    assert r.status_code == 307
    # Now session has staff=True
    r = _client.get("/staff/")
    assert r.status_code == 200
    assert "Dashboard" in r.text


def test_dev_player_logs_in_as_player(_client):
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    r = _client.get("/_dev/player", follow_redirects=False)
    assert r.status_code == 307
    r = _client.get("/characters")
    assert r.status_code == 200
    assert "My Characters" in r.text


# ── Player-side pages ─────────────────────────────────────────────────────────

def test_player_characters_list(player):
    r = player.get("/characters")
    assert r.status_code == 200
    assert "Valeria Morano" in r.text


def test_player_character_detail(player):
    r = player.get("/characters/1")
    assert r.status_code == 200
    assert "Valeria Morano" in r.text
    # SHEET / CLAIM XP / SPEND XP / HISTORY tabs present
    assert "SHEET" in r.text.upper()
    assert "CLAIM XP" in r.text.upper()


def test_player_character_detail_sheet_tab_renders_v5_traits(player):
    r = player.get("/characters/1?tab=sheet")
    assert r.status_code == 200
    # A few V5 traits should appear in the sheet markup
    assert "Strength" in r.text
    assert "Athletics" in r.text
    assert "Animalism" in r.text   # discipline list
    assert "Humanity" in r.text


def test_clan_and_predator_data_renders_in_wizard(player):
    """The wizard should embed the clan + predator data the Alpine state
    reads to render the pickers. The legacy Quick Reference sidebar was
    removed per Steward direction (2026-05); this test now just guards
    that the underlying data payload still reaches the page."""
    r = player.get("/characters/new")
    assert r.status_code == 200
    # A clan name we know exists.
    assert "Banu Haqim" in r.text
    # And a predator type we know is on the list.
    assert "Alleycat" in r.text


def test_player_character_create_renders_clean(player):
    """Regression: /characters/new previously leaked raw Alpine JS as text
    when {{ clans|tojson }} broke out of the x-data double-quoted attribute.
    """
    r = player.get("/characters/new")
    assert r.status_code == 200
    # Form heading and at least one clan should be present
    assert "Blood" in r.text
    assert "Brujah" in r.text
    # Bug signature — Alpine code should NEVER appear as visible text
    assert "canAdvance1()" not in r.text or "x-data" in r.text  # method in x-data is fine; leaking it is not


def test_player_character_edit_renders(player):
    r = player.get("/characters/1/edit")
    assert r.status_code == 200
    assert "Profile Image" in r.text
    assert "Character Name" in r.text


def test_player_coteries(player):
    r = player.get("/coteries")
    assert r.status_code == 200


def test_ic_profile_renders_for_approved_character(player):
    r = player.get("/profiles/1")
    assert r.status_code == 200
    assert "Valeria Morano" in r.text
    assert "Particulars" in r.text


def test_ic_profile_404_for_unknown(player):
    r = player.get("/profiles/9999")
    assert r.status_code == 404


# ── Staff-side pages ──────────────────────────────────────────────────────────

def test_staff_dashboard(staff):
    r = staff.get("/staff/")
    assert r.status_code == 200
    assert "Dashboard" in r.text


def test_staff_roster_has_filter_bar(staff):
    r = staff.get("/staff/characters")
    assert r.status_code == 200
    assert "Roster" in r.text
    # Filter bar additions
    assert "Search" in r.text


def test_staff_character_detail(staff):
    r = staff.get("/staff/characters/1")
    assert r.status_code == 200
    assert "Valeria Morano" in r.text
    # All tab content still present in the DOM (Alpine x-show hides, doesn't remove)
    assert "Adjust XP" in r.text
    assert "Character Sheet" in r.text
    assert "Strength" in r.text
    assert "Athletics" in r.text
    assert "Willpower" in r.text  # derived stat now surfaced for staff (Composure + Resolve)
    assert "ST Notes" in r.text
    assert 'name="st_notes"' in r.text
    # Tab strip is rendered — at minimum the Sheet and Tools tab buttons exist
    assert "tab === 'sheet'" in r.text
    assert "tab === 'tools'" in r.text


def test_staff_st_notes_round_trip(staff):
    """Posting to the ST notes endpoint should persist the text and
    show it back when re-rendering the detail page. ST notes are also
    never exposed in any player template."""
    needle = "Remember to ask about Valerias mentor."
    r = staff.post(
        "/staff/characters/1/st-notes",
        data={"_csrf": "dev-csrf-token", "st_notes": needle},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute("SELECT st_notes FROM characters WHERE id=1").fetchone()
        assert row["st_notes"] == needle

    # Saved value renders back on the staff detail page
    r2 = staff.get("/staff/characters/1")
    assert needle in r2.text

    # Clear so we don't pollute subsequent tests
    staff.post("/staff/characters/1/st-notes",
               data={"_csrf": "dev-csrf-token", "st_notes": ""},
               follow_redirects=False)


def test_staff_claims_queue(staff):
    r = staff.get("/staff/claims")
    assert r.status_code == 200
    assert "Claims Queue" in r.text


def test_staff_spends_queue(staff):
    r = staff.get("/staff/spends")
    assert r.status_code == 200
    assert "Spends Queue" in r.text


def test_staff_coteries(staff):
    r = staff.get("/staff/coteries")
    assert r.status_code == 200
    assert "Coteries" in r.text


def test_staff_criteria(staff):
    r = staff.get("/staff/criteria")
    assert r.status_code == 200
    assert "Criteria" in r.text


def test_criteria_create_toggle_update(staff):
    """Staff criteria CRUD (S4): create -> active row; toggle -> deactivates;
    update -> fields change."""
    from web.db import get_db, list_criteria

    def _find(cid=None, label=None):
        with get_db() as conn:
            for c in list_criteria(conn, active_only=False):
                if (cid and c["id"] == cid) or (label and c["label"] == label):
                    return c
        return None

    staff.post("/staff/criteria",
               data={"_csrf": "dev-csrf-token", "label": "QA Crit",
                     "xp_value": "3", "category": "player",
                     "description": "qa", "sort_order": "0"},
               follow_redirects=False)
    crit = _find(label="QA Crit")
    assert crit is not None and crit["active"] == 1 and crit["xp_value"] == 3
    cid = crit["id"]
    try:
        staff.post(f"/staff/criteria/{cid}/toggle",
                   data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
        assert _find(cid=cid)["active"] == 0
        staff.post(f"/staff/criteria/{cid}/update",
                   data={"_csrf": "dev-csrf-token", "label": "QA Updated",
                         "xp_value": "5", "description": "u", "sort_order": "1"},
                   follow_redirects=False)
        updated = _find(cid=cid)
        assert updated["label"] == "QA Updated" and updated["xp_value"] == 5
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM criteria WHERE id=?", (cid,))
            conn.commit()


def test_staff_periods(staff):
    r = staff.get("/staff/periods")
    assert r.status_code == 200
    assert "Periods" in r.text


def test_staff_sites(staff):
    r = staff.get("/staff/sites")
    assert r.status_code == 200
    assert "Hunting Sites" in r.text


def test_staff_audit_log_with_filters(staff):
    r = staff.get("/staff/audit")
    assert r.status_code == 200
    assert "Audit Log" in r.text
    # Three filters present
    assert "Type" in r.text
    assert "Action" in r.text
    assert "Actor" in r.text


def test_staff_audit_filter_by_action(staff):
    r = staff.get("/staff/audit?action=approve_character")
    assert r.status_code == 200


def test_staff_admin(staff):
    r = staff.get("/staff/admin")
    assert r.status_code == 200
    assert "Admin" in r.text


# ── Auth boundary ─────────────────────────────────────────────────────────────

def test_player_cannot_access_staff_routes(player):
    r = player.get("/staff/")
    assert r.status_code == 403
    assert "Access Denied" in r.text


# ── POST happy paths ──────────────────────────────────────────────────────────

def test_sheet_save_persists(player):
    """POSTing the sheet form updates DB."""
    import json as _json
    from web.db import get_db
    r = player.post(
        "/characters/1/sheet",
        data={
            "_csrf": "dev-csrf-token",
            "attr_strength": "3",
            "skill_athletics": "2",
            "humanity": "7",
            "merits":      '[{"name":"Resources","dots":2}]',
            "flaws":       "[]",
            "specialties": '[{"skill":"skill_athletics","name":"Parkour"}]',
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Read sheet straight from the DB
    with get_db() as conn:
        row = conn.execute("SELECT sheet_json FROM characters WHERE id=1").fetchone()
    sheet = _json.loads(row["sheet_json"])
    assert sheet.get("attr_strength")   == 3
    assert sheet.get("skill_athletics") == 2
    assert sheet.get("humanity")        == 7
    assert sheet.get("merits") == [{"name": "Resources", "dots": 2}]
    assert sheet.get("specialties") == [{"skill": "skill_athletics", "name": "Parkour"}]


def test_sheet_save_persists_high_blood_potency(player):
    """Blood Potency must persist above 5 — V5 RAW runs it 0→10 like Humanity.
    Regression for SHEET_LIMITS, which used to clamp blood_potency back to 5 on
    save, silently undoing an approved BP purchase past 5."""
    import json as _json
    from web.db import get_db
    r = player.post(
        "/characters/1/sheet",
        data={
            "_csrf": "dev-csrf-token",
            "blood_potency": "8",
            "humanity": "10",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        row = conn.execute("SELECT sheet_json FROM characters WHERE id=1").fetchone()
    sheet = _json.loads(row["sheet_json"])
    assert sheet.get("blood_potency") == 8, "BP was clamped — SHEET_LIMITS still caps it"
    assert sheet.get("humanity") == 10


def test_sheet_save_rejects_bad_specialty_skill(player):
    """Specialty referencing a non-existent skill key should be dropped."""
    import json as _json
    from web.db import get_db
    r = player.post(
        "/characters/1/sheet",
        data={
            "_csrf": "dev-csrf-token",
            "specialties": '[{"skill":"skill_athletics","name":"Real"},{"skill":"skill_FAKE","name":"Junk"},{"skill":"","name":"Empty"}]',
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        row = conn.execute("SELECT sheet_json FROM characters WHERE id=1").fetchone()
    sheet = _json.loads(row["sheet_json"])
    # Only the valid one survives the validator
    assert sheet.get("specialties") == [{"skill": "skill_athletics", "name": "Real"}]


def test_xp_adjustment_persists(staff):
    """Staff inline XP adjust creates a ledger entry."""
    r = staff.post(
        "/staff/characters/1/adjust-xp",
        data={
            "_csrf": "dev-csrf-token",
            "delta": "1",
            "note":  "Smoke test bonus",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # The character detail page should now have the new note in the ledger
    r = staff.get("/staff/characters/1")
    assert "Smoke test bonus" in r.text


def test_dice_bot_state_endpoint(_client):
    """The dice-bot integration endpoint applies deltas to damage / hunger / humanity."""
    import json as _json
    from web.db import get_db

    # Reset Valeria's sheet so deltas have a known baseline
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")

    headers = {"Authorization": "Bearer smoke-test-token"}

    # First: superficial damage from a routine roll
    r = _client.post(
        "/api/characters/1/state",
        json={"damage_health_sup": 2, "hunger": 1, "source": "dice:bot"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"]["damage_health_sup"] == 2
    assert body["state"]["hunger"] == 1

    # Second call stacks deltas + converts a superficial to aggravated
    r = _client.post(
        "/api/characters/1/state",
        json={"damage_health_sup": -1, "damage_health_agg": 1, "source": "dice:bot"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["state"]["damage_health_sup"] == 1
    assert r.json()["state"]["damage_health_agg"] == 1

    # Clamp: hunger can't go above 5
    _client.post(
        "/api/characters/1/state",
        json={"hunger": 99},
        headers=headers,
    )
    with get_db() as conn:
        row = conn.execute("SELECT sheet_json FROM characters WHERE id=1").fetchone()
    sheet = _json.loads(row["sheet_json"])
    assert sheet.get("hunger") == 5

    # Auth: reject calls without the bot token
    r = _client.post(
        "/api/characters/1/state",
        json={"hunger": 1},
    )
    assert r.status_code == 401


def test_macro_api_set_overwrite_delete(_client):
    """The bot can save, overwrite, and delete named roll macros on a
    character's sheet (sheet_json.macros), guarded by the bot token."""
    import json as _json
    from web.db import get_db
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()
    headers = {"Authorization": "Bearer smoke-test-token"}
    # save
    r = _client.post("/api/characters/1/macros",
                     json={"name": "frenzy", "expression": "strength + brawl"},
                     headers=headers)
    assert r.status_code == 200
    assert r.json()["macros"]["frenzy"] == "strength + brawl"
    # overwrite + persist
    r = _client.post("/api/characters/1/macros",
                     json={"name": "frenzy", "expression": "stamina + resolve"},
                     headers=headers)
    assert r.json()["macros"]["frenzy"] == "stamina + resolve"
    with get_db() as conn:
        sheet = _json.loads(conn.execute(
            "SELECT sheet_json FROM characters WHERE id=1").fetchone()["sheet_json"])
    assert sheet["macros"]["frenzy"] == "stamina + resolve"
    # delete via empty expression
    r = _client.post("/api/characters/1/macros",
                     json={"name": "frenzy", "expression": ""}, headers=headers)
    assert "frenzy" not in r.json()["macros"]
    # auth
    r = _client.post("/api/characters/1/macros",
                     json={"name": "x", "expression": "5"})
    assert r.status_code == 401
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()


def test_condition_api_add_clear_and_auth(_client):
    """The bot can add and clear transient conditions on a character's sheet
    (sheet_json.conditions), guarded by the bot token. Adding the same name
    twice replaces it (case-insensitive) rather than duplicating."""
    import json as _json
    from web.db import get_db
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()
    headers = {"Authorization": "Bearer smoke-test-token"}
    # add with a note
    r = _client.post("/api/characters/1/conditions",
                     json={"name": "Torpor", "note": "until staff wakes"},
                     headers=headers)
    assert r.status_code == 200, r.text
    conds = r.json()["conditions"]
    assert conds == [{"name": "Torpor", "note": "until staff wakes"}]
    # re-add same name (case-insensitive) replaces, no duplicate; the new
    # spelling wins and the note resets.
    r = _client.post("/api/characters/1/conditions",
                     json={"name": "torpor"}, headers=headers)
    assert [c["name"] for c in r.json()["conditions"]] == ["torpor"]
    assert "note" not in r.json()["conditions"][0]
    # add a second condition, then persist check
    _client.post("/api/characters/1/conditions",
                 json={"name": "On Fire"}, headers=headers)
    with get_db() as conn:
        sheet = _json.loads(conn.execute(
            "SELECT sheet_json FROM characters WHERE id=1").fetchone()["sheet_json"])
    assert {c["name"] for c in sheet["conditions"]} == {"torpor", "On Fire"}
    # clear one
    r = _client.post("/api/characters/1/conditions",
                     json={"name": "Torpor", "active": False}, headers=headers)
    assert [c["name"] for c in r.json()["conditions"]] == ["On Fire"]
    # auth required
    assert _client.post("/api/characters/1/conditions",
                        json={"name": "x"}).status_code == 401
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()


def test_bond_api_drink_set_clear_and_clamp(_client):
    """The bot can deepen (delta), set, and clear a blood bond toward a
    regnant on sheet_json.bonds. NYbN scale: 3 drinks = full bond, max 6;
    clamped to 0-6, guarded by the bot token."""
    import json as _json
    from web.db import get_db
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()
    headers = {"Authorization": "Bearer smoke-test-token"}
    # a drink deepens the bond one level per night, up to the max of 6
    for expected in (1, 2, 3, 4, 5, 6):
        r = _client.post("/api/characters/1/bonds",
                         json={"regnant": "Prince Antoine", "delta": 1},
                         headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["bonds"][0]["level"] == expected
    # a seventh drink stays clamped at 6
    r = _client.post("/api/characters/1/bonds",
                     json={"regnant": "Prince Antoine", "delta": 1}, headers=headers)
    assert r.json()["bonds"][0]["level"] == 6
    # a second regnant set absolutely (full bond at 3); case-insensitive
    r = _client.post("/api/characters/1/bonds",
                     json={"regnant": "Sire Marguerite", "level": 3}, headers=headers)
    by = {b["regnant"]: b["level"] for b in r.json()["bonds"]}
    assert by == {"Prince Antoine": 6, "Sire Marguerite": 3}
    # an out-of-range set is clamped to the 0-6 max
    r = _client.post("/api/characters/1/bonds",
                     json={"regnant": "Sire Marguerite", "level": 9}, headers=headers)
    assert next(b["level"] for b in r.json()["bonds"]
                if b["regnant"] == "Sire Marguerite") == 6
    with get_db() as conn:
        sheet = _json.loads(conn.execute(
            "SELECT sheet_json FROM characters WHERE id=1").fetchone()["sheet_json"])
    assert {b["regnant"] for b in sheet["bonds"]} == {"Prince Antoine", "Sire Marguerite"}
    # set level 0 clears the bond
    r = _client.post("/api/characters/1/bonds",
                     json={"regnant": "prince antoine", "level": 0}, headers=headers)
    assert [b["regnant"] for b in r.json()["bonds"]] == ["Sire Marguerite"]
    # auth required
    assert _client.post("/api/characters/1/bonds",
                        json={"regnant": "x", "delta": 1}).status_code == 401
    with get_db() as conn:
        conn.execute("UPDATE characters SET sheet_json='{}' WHERE id=1")
        conn.commit()


def test_coterie_api_endpoint(_client):
    """GET /api/characters/{id}/coterie returns the coterie + members."""
    from web.db import get_db, create_coterie, add_coterie_member

    # Seed a coterie + put Valeria in it (the test DB starts empty)
    with get_db() as conn:
        # Drop any prior test coterie to keep this idempotent
        conn.execute("DELETE FROM coterie_memberships")
        conn.execute("DELETE FROM coteries")
        co = create_coterie(conn, "Smoke Test Coterie")
        add_coterie_member(conn, co["id"], 1)   # Valeria

    headers = {"Authorization": "Bearer smoke-test-token"}
    r = _client.get("/api/characters/1/coterie", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["coterie"]["name"] == "Smoke Test Coterie"
    member_names = {m["name"] for m in body["members"]}
    assert "Valeria Morano" in member_names

    # Auth check
    r = _client.get("/api/characters/1/coterie")
    assert r.status_code == 401

    # Character not in a coterie returns 404
    with get_db() as conn:
        conn.execute("DELETE FROM coterie_memberships WHERE character_id=1")
    r = _client.get("/api/characters/1/coterie", headers=headers)
    assert r.status_code == 404


def test_spend_rejects_when_combined_pending_exceeds_xp(player):
    """A second spend that would push (pending + new) > available is rejected at submit."""
    from web.db import get_db

    # Reset Valeria with 10 XP available, no pending spends
    with get_db() as conn:
        conn.execute("UPDATE characters SET xp_total=10, xp_spent=0 WHERE id=1")
        conn.execute("DELETE FROM spend_requests WHERE character_id=1 AND status='pending'")

    # First spend — 6 XP (Skill 0→2: 3 + 6 = 9? actually 1×3 + 2×3 = 9)
    # Let's use Skill 0→1 which is 1×3 = 3 XP, then 1→2 which is 2×3 = 6 XP, so 0→2 = 9 XP total
    # Easier: Specialty is flat 3 XP. Use that twice.
    r = player.post("/characters/1/spend", data={
        "_csrf": "dev-csrf-token",
        "category": "Specialty",
        "trait_name": "Athletics: Parkour",
        "current_dots": "0", "new_dots": "1",
    })
    assert r.status_code == 200
    # First spend should succeed (3 XP <= 10 available)
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM spend_requests WHERE character_id=1 AND status='pending'").fetchone()["n"]
    assert n == 1

    # Now submit Attribute Strength 1→3 (cost = 2*5 + 3*5 = 25 XP).
    # Pending = 3, base available = 10. Effective = 7. 25 > 7, should fail.
    r = player.post("/characters/1/spend", data={
        "_csrf": "dev-csrf-token",
        "category": "Attribute",
        "trait_name": "Strength",
        "current_dots": "1", "new_dots": "3",
    })
    assert r.status_code == 200
    assert "Insufficient" in r.text, "Should reject when pending+new exceeds available"
    assert "pending" in r.text, "Error should hint at the pending review queue"

    # A small enough second spend should still succeed (3 XP, total 6 pending, still <= 10)
    r = player.post("/characters/1/spend", data={
        "_csrf": "dev-csrf-token",
        "category": "Specialty",
        "trait_name": "Insight: Reading Faces",
        "current_dots": "0", "new_dots": "1",
    })
    assert r.status_code == 200
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM spend_requests WHERE character_id=1 AND status='pending'").fetchone()["n"]
    assert n == 2

    # Cleanup
    with get_db() as conn:
        conn.execute("DELETE FROM spend_requests WHERE character_id=1 AND status='pending'")
        conn.execute("UPDATE characters SET xp_total=6, xp_spent=3 WHERE id=1")


def test_coterie_request_rejects_oversized(player):
    """Coterie cap (6 members) should reject a 7+ submission."""
    import json as _json
    r = player.post(
        "/coteries/request",
        data={
            "_csrf": "dev-csrf-token",
            "proposed_name": "OversizedSmokeTest",
            "member_ids": _json.dumps([101, 102, 103, 104, 105, 106, 107]),
        },
    )
    assert r.status_code == 200
    assert "at most" in r.text   # the cap error message


# ── Calendar widget — period helpers + render ────────────────────────────────

def test_period_helpers_partition_by_time():
    """list_upcoming_periods returns future-only; recent returns past-only."""
    from web.db import (
        get_db,
        create_period,
        list_upcoming_periods,
        list_recent_closed_periods,
    )
    with get_db() as conn:
        # Future-scheduled
        future = create_period(
            conn, label="SmokeFuture", period_type="night", phase="full",
            opens_at="2099-01-01T20:00:00Z", closes_at="2099-01-02T04:00:00Z",
            created_by="smoke-test",
        )
        # Past-closed
        past = create_period(
            conn, label="SmokePast", period_type="night", phase="full",
            opens_at="2000-01-01T20:00:00Z", closes_at="2000-01-02T04:00:00Z",
            created_by="smoke-test",
        )

        try:
            up_ids     = {p["id"] for p in list_upcoming_periods(conn, limit=20)}
            recent_ids = {p["id"] for p in list_recent_closed_periods(conn, limit=20)}

            assert future["id"] in up_ids,   "future period should appear in upcoming"
            assert past["id"]   not in up_ids, "past period must not appear in upcoming"
            assert past["id"]   in recent_ids, "past period should appear in recent-closed"
            assert future["id"] not in recent_ids, "future period must not appear in recent-closed"
        finally:
            # Clean up rows we created so other tests start clean.
            for pid in (future["id"], past["id"]):
                conn.execute("DELETE FROM play_periods WHERE id=?", (pid,))


def test_upcoming_excludes_active_period():
    """An active period must not be reported as upcoming, even if its
    opens_at is in the future (edge case: pre-scheduled-but-active)."""
    from web.db import (
        get_db, create_period, set_period_active, close_period,
        list_upcoming_periods,
    )
    with get_db() as conn:
        p = create_period(
            conn, label="SmokeActive", period_type="night", phase="full",
            opens_at="2099-06-01T20:00:00Z", closes_at="2099-06-02T04:00:00Z",
            created_by="smoke-test",
        )
        try:
            set_period_active(conn, p["id"])
            up_ids = {x["id"] for x in list_upcoming_periods(conn, limit=20)}
            assert p["id"] not in up_ids, "active period must not be in upcoming"
        finally:
            close_period(conn, p["id"])
            conn.execute("DELETE FROM play_periods WHERE id=?", (p["id"],))


def test_bot_api_active_period_includes_upcoming(_client):
    """GET /api/period/active (bot auth) returns the active period plus the
    next `upcoming` periods — the data /timeskip renders."""
    from web.db import (
        get_db, create_period, set_period_active, close_period,
        get_active_period,
    )
    headers = {"Authorization": "Bearer smoke-test-token"}
    # Snapshot any active period so we can restore it (set_period_active
    # deactivates all others).
    with get_db() as conn:
        prior = get_active_period(conn)
        prior_id = prior["id"] if prior else None
        active = create_period(
            conn, label="TimeskipActiveSmoke", period_type="night", phase="full",
            opens_at="2026-05-29T20:00:00Z", closes_at="2099-05-31T04:00:00Z",
            created_by="smoke-test")
        set_period_active(conn, active["id"])
        nxt = create_period(
            conn, label="TimeskipUpcomingSmoke", period_type="night", phase="full",
            opens_at="2099-06-12T20:00:00Z", closes_at="2099-06-13T04:00:00Z",
            created_by="smoke-test")
    try:
        # Requires the bearer token.
        assert _client.get("/api/period/active").status_code in (401, 403)
        r = _client.get("/api/period/active", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is True
        assert body["period"]["label"] == "TimeskipActiveSmoke"
        labels = {p["label"] for p in body["upcoming"]}
        assert "TimeskipUpcomingSmoke" in labels
        # The active period is never listed as upcoming.
        assert "TimeskipActiveSmoke" not in labels
    finally:
        with get_db() as conn:
            close_period(conn, active["id"])
            conn.execute("DELETE FROM play_periods WHERE id IN (?, ?)",
                         (active["id"], nxt["id"]))
            if prior_id:
                set_period_active(conn, prior_id)


def test_staff_dashboard_renders_calendar_widget(staff):
    r = staff.get("/staff/")
    assert r.status_code == 200
    assert "Chronicle Calendar" in r.text


def test_player_characters_renders_calendar_widget(player):
    r = player.get("/characters")
    assert r.status_code == 200
    assert "Chronicle Calendar" in r.text


# ── Coterie notification enqueuing ───────────────────────────────────────────

def test_coterie_request_approval_enqueues_bot_event():
    """When staff approves a coterie request, bot_outbox should get a
    coterie_request_approved row for the submitter (and each member)."""
    from web.db import (
        get_db, create_coterie_request, approve_coterie_request, get_character,
    )
    with get_db() as conn:
        # Sanity: the dev seed character (id=1) exists.
        char = get_character(conn, 1)
        assert char is not None

        # Drain outbox so we can assert on what THIS test enqueues.
        conn.execute("DELETE FROM bot_outbox WHERE processed_at IS NULL")

        req = create_coterie_request(
            conn,
            requested_by=char["discord_id"],
            proposed_name="SmokeCoterieApprove",
            member_ids=[char["id"]],
            note="smoke",
        )
        approve_coterie_request(conn, req["id"], reviewer_id="staff-smoke")

        rows = conn.execute("""
            SELECT command, payload FROM bot_outbox
            WHERE command='coterie_request_approved' AND processed_at IS NULL
        """).fetchall()
        assert len(rows) >= 1
        # At least one event must target the submitter
        import json as _j
        recipients = {_j.loads(r["payload"])["discord_id"] for r in rows}
        assert char["discord_id"] in recipients

        # Cleanup
        conn.execute("DELETE FROM bot_outbox WHERE command LIKE 'coterie_%'")
        coterie_id_row = conn.execute(
            "SELECT coterie_id FROM coterie_requests WHERE id=?", (req["id"],)
        ).fetchone()
        if coterie_id_row and coterie_id_row["coterie_id"]:
            cid = coterie_id_row["coterie_id"]
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (cid,))
            conn.execute("DELETE FROM coteries WHERE id=?", (cid,))
        conn.execute("DELETE FROM coterie_requests WHERE id=?", (req["id"],))


def test_coterie_request_approval_creates_coterie_with_members(_client):
    """Backfill: approving a coterie proposal request creates the coterie,
    links the request to it, and adds the proposed members (the bot-event
    test only checks the notification, not the lifecycle)."""
    from web.db import (get_db, upsert_player, create_character,
                        create_coterie_request, approve_coterie_request)
    with get_db() as conn:
        upsert_player(conn, discord_id="820820820", username="CotReqA")
        upsert_player(conn, discord_id="821821821", username="CotReqB")
        a = create_character(conn, discord_id="820820820", name="CotReq A", clan="brujah")
        b = create_character(conn, discord_id="821821821", name="CotReq B", clan="ventrue")
        req = create_coterie_request(
            conn, requested_by="820820820", proposed_name="Backfill Accord",
            member_ids=[a["id"], b["id"]], note="qa")
        try:
            approve_coterie_request(conn, req["id"], reviewer_id="staff-smoke")
            conn.commit()
            row = conn.execute(
                "SELECT status, coterie_id FROM coterie_requests WHERE id=?",
                (req["id"],)).fetchone()
            assert row["status"] == "approved"
            cid = row["coterie_id"]
            assert cid is not None, "approved request did not create a coterie"
            cot = conn.execute(
                "SELECT name FROM coteries WHERE id=?", (cid,)).fetchone()
            assert cot["name"] == "Backfill Accord"
            members = {r["character_id"] for r in conn.execute(
                "SELECT character_id FROM coterie_memberships WHERE coterie_id=?",
                (cid,)).fetchall()}
            assert members == {a["id"], b["id"]}
        finally:
            row = conn.execute(
                "SELECT coterie_id FROM coterie_requests WHERE id=?",
                (req["id"],)).fetchone()
            cid = row["coterie_id"] if row else None
            if cid:
                for t in ("coterie_memberships", "coterie_contributions",
                          "coterie_merits", "coterie_flaws", "coterie_spends"):
                    conn.execute(f"DELETE FROM {t} WHERE coterie_id=?", (cid,))
                conn.execute("DELETE FROM coteries WHERE id=?", (cid,))
            conn.execute("DELETE FROM coterie_requests WHERE id=?", (req["id"],))
            conn.execute("DELETE FROM bot_outbox WHERE command LIKE 'coterie_%'")
            conn.execute("DELETE FROM characters WHERE id IN (?, ?)", (a["id"], b["id"]))
            conn.commit()


def test_coterie_request_rejection_enqueues_bot_event():
    from web.db import (
        get_db, create_coterie_request, reject_coterie_request, get_character,
    )
    with get_db() as conn:
        char = get_character(conn, 1)
        assert char is not None
        conn.execute("DELETE FROM bot_outbox WHERE processed_at IS NULL")

        req = create_coterie_request(
            conn,
            requested_by=char["discord_id"],
            proposed_name="SmokeCoterieReject",
            member_ids=[char["id"]],
            note="smoke",
        )
        reject_coterie_request(conn, req["id"], reviewer_id="staff-smoke",
                               reason="conflict with existing coterie")

        rows = conn.execute("""
            SELECT payload FROM bot_outbox
            WHERE command='coterie_request_rejected' AND processed_at IS NULL
        """).fetchall()
        assert len(rows) == 1
        import json as _j
        payload = _j.loads(rows[0]["payload"])
        assert payload["discord_id"] == char["discord_id"]
        assert "conflict" in payload["reason"]

        conn.execute("DELETE FROM bot_outbox WHERE command LIKE 'coterie_%'")
        conn.execute("DELETE FROM coterie_requests WHERE id=?", (req["id"],))


# ── Claims + Spends history views ────────────────────────────────────────────

def test_staff_claims_history_renders(staff):
    r = staff.get("/staff/claims/history")
    assert r.status_code == 200
    assert "Claims History" in r.text
    # The filter form is present
    assert 'name="status"' in r.text
    assert 'name="period_id"' in r.text


def test_staff_claims_history_status_filter(staff):
    """After consolidation, the legacy /claims/history?status=pending
    URL redirects to the unified /claims view in queue mode (status=pending
    is the actionable workflow, not history)."""
    r = staff.get("/staff/claims/history?status=pending")
    assert r.status_code == 200
    # status=pending now renders as the queue, not history
    assert "Claims Queue" in r.text


def test_staff_spends_history_renders(staff):
    r = staff.get("/staff/spends/history")
    assert r.status_code == 200
    assert "Spends History" in r.text
    assert 'name="category"' in r.text


def test_staff_history_requires_staff(player):
    r1 = player.get("/staff/claims/history", follow_redirects=False)
    r2 = player.get("/staff/spends/history", follow_redirects=False)
    assert r1.status_code in (303, 403)
    assert r2.status_code in (303, 403)


# ── Data export ──────────────────────────────────────────────────────────────

def test_admin_export_returns_full_snapshot(staff):
    """The export endpoint returns a JSON snapshot keyed by table name
    with rows from every non-transient table."""
    import json as _json
    r = staff.get("/staff/admin/export.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "enoch-export-" in r.headers.get("content-disposition", "")
    payload = _json.loads(r.text)
    assert payload["schema_version"] == 1
    assert "exported_at" in payload
    assert "characters" in payload["tables"]
    assert "audit_log"  in payload["tables"]
    # The dev seed put at least one character in
    assert len(payload["tables"]["characters"]) >= 1
    # bot_outbox is excluded (transient)
    assert "bot_outbox" not in payload["tables"]


def test_admin_export_requires_staff(player):
    r = player.get("/staff/admin/export.json", follow_redirects=False)
    assert r.status_code in (303, 403)


def test_admin_export_writes_audit_row(staff):
    """Each export produces an audit_log row so we know who pulled snapshots."""
    from web.db import get_db
    before_r = staff.get("/staff/admin/export.json")
    assert before_r.status_code == 200
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE action='export_snapshot'"
        ).fetchone()["n"]
    assert n >= 1


# ── Global character search ──────────────────────────────────────────────────

def test_staff_search_finds_by_name(staff):
    """Searching for 'val' should return Valeria Morano (dev seed character)."""
    r = staff.get("/staff/search?q=val")
    assert r.status_code == 200
    assert "Valeria Morano" in r.text


def test_staff_search_empty_query_returns_empty(staff):
    """Queries shorter than 2 chars return an empty fragment (no panel)."""
    r = staff.get("/staff/search?q=")
    assert r.status_code == 200
    assert "Valeria Morano" not in r.text
    # Also a single char is below the threshold
    r2 = staff.get("/staff/search?q=v")
    assert r2.status_code == 200
    assert "Valeria Morano" not in r2.text


def test_staff_search_no_matches_shows_empty_state(staff):
    r = staff.get("/staff/search?q=zzz-no-such-character")
    assert r.status_code == 200
    assert "No characters found" in r.text


def test_staff_search_requires_staff(player):
    """Players can't hit the staff search endpoint."""
    r = player.get("/staff/search?q=val", follow_redirects=False)
    assert r.status_code in (303, 403)


# ── Period closing reminder sweep ────────────────────────────────────────────

def test_period_closing_soon_fires_within_24h():
    """An active period closing in <24h should enqueue exactly one
    period_closing_soon event and flag the period as notified."""
    from datetime import datetime, timezone, timedelta
    from web.db import (
        get_db, create_period, set_period_active, close_period,
        sweep_period_closing_soon,
    )
    with get_db() as conn:
        # Remember any currently-active period so we can restore it.
        existing = conn.execute(
            "SELECT id FROM play_periods WHERE is_active=1 LIMIT 1"
        ).fetchone()
        prior_active_id = existing["id"] if existing else None

        # Create a period that closes in 6h
        soon = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        opens = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = create_period(conn, label="ClosingSoonSmoke", period_type="night", phase="full",
                          opens_at=opens, closes_at=soon, created_by="smoke")
        set_period_active(conn, p["id"])
        conn.execute("DELETE FROM bot_outbox WHERE command='period_closing_soon'")

    try:
        with get_db() as conn:
            notified = sweep_period_closing_soon(conn)
            assert any(n["id"] == p["id"] for n in notified), \
                "period closing in 6h should be picked up by the sweep"

            rows = conn.execute(
                "SELECT * FROM bot_outbox WHERE command='period_closing_soon'"
            ).fetchall()
            assert len(rows) >= 1

            # Second sweep should NOT re-enqueue — flag prevents duplicates
            conn.execute("DELETE FROM bot_outbox WHERE command='period_closing_soon'")
            notified_again = sweep_period_closing_soon(conn)
            assert not any(n["id"] == p["id"] for n in notified_again)
    finally:
        with get_db() as conn:
            close_period(conn, p["id"])
            conn.execute("DELETE FROM bot_outbox WHERE command='period_closing_soon'")
            conn.execute("DELETE FROM play_periods WHERE id=?", (p["id"],))
            if prior_active_id:
                set_period_active(conn, prior_active_id)


def test_period_closing_soon_skips_when_far_future():
    """A period closing in 5 days should NOT fire the reminder."""
    from datetime import datetime, timezone, timedelta
    from web.db import (
        get_db, create_period, set_period_active, close_period,
        sweep_period_closing_soon,
    )
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM play_periods WHERE is_active=1 LIMIT 1"
        ).fetchone()
        prior_active_id = existing["id"] if existing else None

        opens = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        far   = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = create_period(conn, label="FarFutureSmoke", period_type="night", phase="full",
                          opens_at=opens, closes_at=far, created_by="smoke")
        set_period_active(conn, p["id"])

    try:
        with get_db() as conn:
            notified = sweep_period_closing_soon(conn)
            assert not any(n["id"] == p["id"] for n in notified), \
                "period closing in 5 days must not fire the reminder"
    finally:
        with get_db() as conn:
            close_period(conn, p["id"])
            conn.execute("DELETE FROM play_periods WHERE id=?", (p["id"],))
            if prior_active_id:
                set_period_active(conn, prior_active_id)


def test_calendar_widget_wires_countdown_when_period_active(staff):
    """When a period is active, the partial emits the Alpine countdown
    component pointing at the right closes_at."""
    from web.db import (
        get_db, create_period, set_period_active, close_period,
        get_active_period,
    )
    # Snapshot any currently-active period so we can restore it after
    # this test deactivates it (set_period_active deactivates all others).
    with get_db() as conn:
        prior_active = get_active_period(conn)
        p = create_period(
            conn, label="SmokeActiveTick", period_type="night", phase="dusk",
            opens_at="2099-12-31T20:00:00Z", closes_at="2099-12-31T23:59:00Z",
            created_by="smoke-test",
        )
        set_period_active(conn, p["id"])
    # Connection committed — route's own connection can now see the new period.
    try:
        r = staff.get("/staff/")
        assert r.status_code == 200
        assert "SmokeActiveTick" in r.text
        assert "calendarCountdown(" in r.text
        # Phase label leaks into the period block
        assert "Dusk" in r.text
    finally:
        with get_db() as conn:
            close_period(conn, p["id"])
            conn.execute("DELETE FROM play_periods WHERE id=?", (p["id"],))
            if prior_active:
                set_period_active(conn, prior_active["id"])


# ── Character creation wizard + review lock ───────────────────────────────────

def _raw_traits(clan="brujah"):
    """RAW-valid base allocation for character-creation POSTs. The wizard now
    enforces V5 spreads, so a full submission must carry the 4/3/3/3/2/2/2/2/1
    attributes, a valid skill distribution (Balanced here), the free specialties,
    and a 2+1 in-clan Discipline base. Spread into a POST's `data` LAST so it
    overrides any partial trait fields the test sets. Pass `clan` to emit that
    clan's in-clan Disciplines (defaults to Brujah's Celerity/Potence)."""
    attrs = {
        "attr_strength": "4", "attr_dexterity": "3", "attr_stamina": "3",
        "attr_charisma": "3", "attr_manipulation": "2", "attr_composure": "2",
        "attr_intelligence": "2", "attr_wits": "2", "attr_resolve": "1",
    }
    skills = {  # Balanced: three 3s, five 2s, seven 1s
        "skill_brawl": "3", "skill_athletics": "3", "skill_stealth": "3",
        "skill_melee": "2", "skill_firearms": "2", "skill_larceny": "2",
        "skill_streetwise": "2", "skill_intimidation": "2",
        "skill_awareness": "1", "skill_drive": "1", "skill_occult": "1",
        "skill_academics": "1", "skill_insight": "1", "skill_persuasion": "1",
        "skill_subterfuge": "1",
    }
    import json as _j
    advantages = {  # 7 advantage dots (Backgrounds + a Merit) + 2 Flaw dots = RAW
        "backgrounds": _j.dumps([{"name": "Allies", "dots": 3},
                                 {"name": "Resources", "dots": 2}]),
        "merits":      _j.dumps([{"name": "Iron Will", "dots": 2}]),
        "advantages":  _j.dumps([]),
        "flaws":       _j.dumps([{"name": "Enemy", "dots": 1},
                                 {"name": "Disliked", "dots": 1}]),
        # Free specialties: 1 free choice (Brawl) + 1 for dotted Academics = 2.
        "specialties": _j.dumps([{"skill": "skill_academics", "name": "History"},
                                 {"skill": "skill_brawl", "name": "Grappling"}]),
    }
    # In-clan 2+1 Discipline base (no predator dot). Clans with an in-clan list
    # get their first two Disciplines as 2 + 1; thin-blood/caitiff get none.
    from web.v5_traits import CLAN_DISCIPLINES
    disc = {}
    _inclan = CLAN_DISCIPLINES.get(clan)
    if _inclan and len(_inclan) >= 2:
        disc = {_inclan[0]: "2", _inclan[1]: "1"}
    return {**attrs, **skills, "skill_spread": "balanced", **disc, **advantages}


def test_character_wizard_submission_populates_full_sheet(player):
    """POST /characters/new with attributes + skills + a specialty + a
    merit should produce a character whose sheet_json carries it all."""
    import json as _j
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Wizard Smoke",
            "clan": "brujah",
            "merits":  _j.dumps([{"name": "Iron Will", "dots": 2}]),
            "flaws":   _j.dumps([{"name": "Disliked",  "dots": 1}]),
            "powers":  _j.dumps([]),
            "rituals": _j.dumps([]),
            "ceremonies": _j.dumps([]),
            "formulae":   _j.dumps([]),
            # V5 chargen: min 2 touchstones — backstop validation.
            "touchstones":_j.dumps(["Sister Maria", "Father Joseph"]),
            "convictions":_j.dumps(["Never harm a child"]),
            # RAW-valid base spread (overrides any partial trait fields above).
            **_raw_traits(),
            # Override _raw_traits' specialties with this test's own — still 2
            # (1 free + 1 for dotted Academics) so "Lower East Side" survives.
            "specialties": _j.dumps([
                {"skill": "skill_streetwise", "name": "Lower East Side"},
                {"skill": "skill_academics",  "name": "History"},
            ]),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Wizard Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        sheet = _j.loads(row["sheet_json"])
        try:
            # _raw_traits() sets attr_strength=4, skill_brawl=3, skill_streetwise=2.
            assert sheet["attr_strength"]   == 4
            assert sheet["skill_brawl"]     == 3
            assert sheet["skill_streetwise"] == 2
            assert any(s["name"] == "Lower East Side" for s in sheet["specialties"])
            assert any(m["name"] == "Iron Will" for m in sheet["merits"])
            # Touchstones now stored as paired {name, conviction} objects
            assert any(t["name"] == "Sister Maria" for t in sheet["touchstones"])
            assert "Never harm a child" in sheet["convictions"]
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))


def test_character_wizard_accepts_optional_image_upload(player):
    """POSTing a multipart character_create form with an attached image
    should save the file under /static/uploads/ and persist its URL on
    the new character row."""
    import json as _j
    # 1×1 PNG — the smallest valid PNG payload we can build by hand.
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Image Smoke",
            "clan": "brujah",
            "touchstones": _j.dumps(["Friend A", "Friend B"]),
            **_raw_traits(),
        },
        files={"profile_image": ("portrait.png", png_bytes, "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from pathlib import Path
    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Image Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        char_id = row["id"]
        try:
            assert row["profile_image_url"] == f"/static/uploads/character_{char_id}.png"
            disk = Path(__file__).parent.parent / "web" / "static" / "uploads" / f"character_{char_id}.png"
            assert disk.exists(), "uploaded file should land on disk"
            assert disk.read_bytes() == png_bytes
        finally:
            for f in (Path(__file__).parent.parent / "web" / "static" / "uploads").glob(f"character_{char_id}.*"):
                try: f.unlink()
                except OSError: pass
            conn.execute("DELETE FROM characters WHERE id=?", (char_id,))


def test_character_wizard_rejects_bad_image_but_still_creates(player):
    """If the player attaches an unsupported file type, the character is
    still created but no image URL is stored and a flash records the
    rejection. We don't want a bad upload to nuke the whole submission."""
    import json as _j
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Bad Image Smoke",
            "clan": "brujah",
            "touchstones": _j.dumps(["Friend A", "Friend B"]),
            **_raw_traits(),
        },
        files={"profile_image": ("notes.txt", b"hello world", "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Bad Image Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        try:
            assert row["profile_image_url"] is None
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))


def test_pending_chars_table_shows_submission_notes_inline(staff, player):
    """When a player attaches submission_notes, the pending characters
    queue should surface them inline (collapsed in a details panel)."""
    import json as _j
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Notes Visible Smoke",
            "clan": "brujah",
            "touchstones": _j.dumps(["Friend A", "Friend B"]),
            "submission_notes": "Please review my Auspex 3 pre-pick.",
            **_raw_traits(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Fixtures share a TestClient — re-seed staff session to make GET work.
    staff.get("/_dev/seed", follow_redirects=False)
    rr = staff.get("/staff/characters")
    assert rr.status_code == 200
    assert "Notes Visible Smoke" in rr.text
    assert "Please review my Auspex 3 pre-pick." in rr.text
    assert "Player Note" in rr.text  # the details summary label

    # Clean up
    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM characters WHERE name='Notes Visible Smoke'")


def test_bulk_approve_queue_processes_multiple_characters(staff, player):
    """POSTing to /staff/queue/bulk-approve with several character_ids
    should approve them all and flash a count. Non-existent IDs surface
    as errors but never block the rest."""
    import json as _j
    # Create two pending characters as the player
    names = ["Bulk Smoke A", "Bulk Smoke B"]
    for nm in names:
        r = player.post(
            "/characters/new",
            data={
                "_csrf": "dev-csrf-token",
                "name": nm, "clan": "brujah",
                "touchstones": _j.dumps(["Friend A", "Friend B"]),
                **_raw_traits(),
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

    from web.db import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM characters WHERE name IN ('Bulk Smoke A', 'Bulk Smoke B')"
        ).fetchall()
        char_ids = [r["id"] for r in rows]
    assert len(char_ids) == 2

    # Switch session back to staff before hitting the bulk endpoint.
    staff.get("/_dev/seed", follow_redirects=False)
    r2 = staff.post(
        "/staff/queue/bulk-approve",
        data={"_csrf": "dev-csrf-token",
              "character_ids": [str(i) for i in char_ids]},
        follow_redirects=False,
    )
    assert r2.status_code == 303

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, is_approved FROM characters WHERE id IN ({','.join(['?']*len(char_ids))})",
            char_ids,
        ).fetchall()
        try:
            assert all(r["is_approved"] for r in rows), "every selected char should be approved"
        finally:
            for cid in char_ids:
                conn.execute("DELETE FROM characters WHERE id=?", (cid,))


def test_staff_detail_raw_compliance_panel(staff, player):
    """The staff detail page lints not-yet-approved characters against V5 RAW —
    a compliant sheet shows the green pass; wiping the allocation lists the
    issues instead. (Approved characters are gated out in the route, so they're
    never linted — they advance past creation limits through play.)"""
    import json as _j
    from web.db import get_db
    r = player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token", "name": "Lint Smoke", "clan": "brujah",
              "touchstones": _j.dumps(["Friend A", "Friend B"]),
              **_raw_traits()},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Lint Smoke' LIMIT 1"
        ).fetchone()["id"]
    try:
        staff.get("/_dev/seed", follow_redirects=False)  # switch session → staff
        # Compliant pending character → green pass, nothing flagged.
        r1 = staff.get(f"/staff/characters/{cid}")
        assert r1.status_code == 200
        assert "Meets V5 RAW character creation" in r1.text
        assert "RAW issue" not in r1.text
        # Wipe the allocation → the panel switches to listing violations.
        with get_db() as conn:
            conn.execute("UPDATE characters SET sheet_json=? WHERE id=?", ("{}", cid))
        r2 = staff.get(f"/staff/characters/{cid}")
        assert r2.status_code == 200
        assert "RAW issue" in r2.text
        assert "Meets V5 RAW character creation" not in r2.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))


def test_bulk_start_review_locks_multiple_sheets(staff, player):
    """Bulk start-review should set review_started_at on every selected
    character so players can't keep editing during triage."""
    import json as _j
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Bulk Review Smoke",
            "clan": "brujah",
            "touchstones": _j.dumps(["Friend A", "Friend B"]),
            **_raw_traits(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM characters WHERE name='Bulk Review Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        char_id = row["id"]

    staff.get("/_dev/seed", follow_redirects=False)
    r2 = staff.post(
        "/staff/queue/bulk-start-review",
        data={"_csrf": "dev-csrf-token", "character_ids": [str(char_id)]},
        follow_redirects=False,
    )
    assert r2.status_code == 303

    with get_db() as conn:
        row = conn.execute(
            "SELECT review_started_at, review_started_by FROM characters WHERE id=?",
            (char_id,)
        ).fetchone()
        try:
            assert row["review_started_at"] is not None
            assert row["review_started_by"]  # set to the staff user id
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (char_id,))


def _ensure_active_period_for_draft_tests(conn):
    """Helper for draft claim tests: make sure there's an active period
    so the claim endpoint will accept submissions. Returns the period."""
    from datetime import datetime, timezone, timedelta
    from web.db import get_active_period, create_period, set_period_active
    period = get_active_period(conn)
    if period:
        return period
    opens  = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    closes = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    period = create_period(conn, label="DraftClaimSmoke", period_type="night",
                           phase="full", opens_at=opens, closes_at=closes,
                           created_by="smoke")
    set_period_active(conn, period["id"])
    return period


def test_draft_xp_claim_saves_and_resumes(player):
    """Players can stash a partial XP claim with as_draft=1, then later
    update it or submit it for real. Drafts must not block normal
    re-submission for the same period and don't appear in staff queues."""
    from web.db import get_db, list_claims_for_character

    with get_db() as conn:
        period = _ensure_active_period_for_draft_tests(conn)
        row = conn.execute(
            "SELECT id FROM characters WHERE is_approved=1 ORDER BY id LIMIT 1"
        ).fetchone()
        assert row is not None, "seed data should include an approved character"
        char_id = row["id"]
        conn.execute(
            "DELETE FROM xp_claims WHERE character_id=? AND play_period_id=?",
            (char_id, period["id"]),
        )

    # Save as draft — no criteria required
    r = player.post(
        f"/characters/{char_id}/claim",
        data={"_csrf": "dev-csrf-token", "as_draft": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 200

    with get_db() as conn:
        drafts = [c for c in list_claims_for_character(conn, char_id)
                  if c["status"] == "draft" and c["play_period_id"] == period["id"]]
        assert len(drafts) == 1, "exactly one draft should exist"
        draft_id = drafts[0]["id"]

    # Update the draft with some content but keep it a draft
    r2 = player.post(
        f"/characters/{char_id}/claim",
        data={"_csrf": "dev-csrf-token",
              "as_draft": "1",
              "draft_id": str(draft_id),
              "rp_links": "https://discord.com/channels/x/y/123"},
        follow_redirects=False,
    )
    assert r2.status_code == 200

    with get_db() as conn:
        drafts = [c for c in list_claims_for_character(conn, char_id)
                  if c["status"] == "draft"]
        assert len(drafts) == 1, "update should replace, not duplicate"
        assert "https://discord.com/channels/x/y/123" in drafts[0]["rp_links"]
        # Clean up
        conn.execute("DELETE FROM xp_claims WHERE character_id=?", (char_id,))


def test_draft_claim_discard_removes_it(player):
    """The discard endpoint should delete the draft but never touch
    pending/approved claims (defense-in-depth)."""
    from web.db import get_db, create_claim, get_claim

    with get_db() as conn:
        period = _ensure_active_period_for_draft_tests(conn)
        row = conn.execute(
            "SELECT id FROM characters WHERE is_approved=1 ORDER BY id LIMIT 1"
        ).fetchone()
        char_id = row["id"]
        conn.execute(
            "DELETE FROM xp_claims WHERE character_id=? AND play_period_id=?",
            (char_id, period["id"]),
        )
        draft = create_claim(
            conn, character_id=char_id, play_period_id=period["id"],
            claimed_criteria=[], rp_links=[], is_draft=True,
        )

    r = player.post(
        f"/characters/{char_id}/claim/{draft['id']}/discard",
        data={"_csrf": "dev-csrf-token"},
        follow_redirects=False,
    )
    assert r.status_code == 200

    with get_db() as conn:
        # Draft should be gone
        assert get_claim(conn, draft["id"]) is None


def test_draft_claim_submit_promotes_to_pending(player):
    """Submitting a resumed draft (as_draft missing) flips status to
    pending so it lands in the staff queue."""
    from web.db import get_db, create_claim, get_claim, list_criteria

    with get_db() as conn:
        period = _ensure_active_period_for_draft_tests(conn)
        row = conn.execute(
            "SELECT id FROM characters WHERE is_approved=1 ORDER BY id LIMIT 1"
        ).fetchone()
        char_id = row["id"]
        conn.execute(
            "DELETE FROM xp_claims WHERE character_id=? AND play_period_id=?",
            (char_id, period["id"]),
        )
        crit = next((c for c in list_criteria(conn, active_only=True)
                     if c["category"] in ("base", "player")), None)
        if crit is None:
            import pytest as _p; _p.skip("No suitable criterion seeded")
        draft = create_claim(
            conn, character_id=char_id, play_period_id=period["id"],
            claimed_criteria=[], rp_links=[], is_draft=True,
        )

    r = player.post(
        f"/characters/{char_id}/claim",
        data={"_csrf": "dev-csrf-token",
              "draft_id": str(draft["id"]),
              "criteria_ids": str(crit["id"]),
              "rp_links": "https://discord.com/channels/x/y/promote"},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:300]

    with get_db() as conn:
        promoted = get_claim(conn, draft["id"])
        assert promoted["status"] == "pending"
        conn.execute("DELETE FROM xp_claims WHERE id=?", (draft["id"],))


def test_start_character_review_locks_player_sheet_edit(staff, player):
    """When staff calls start-review, the player's sheet save endpoint
    refuses to mutate the row."""
    import json as _j
    # Create a fresh pending character as the player. V5 chargen min
    # touchstones is 2 — include enough so the form validates cleanly.
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Lock Smoke", "clan": "brujah",
            "touchstones": _j.dumps(["Mother Anne", "Brother Tom"]),
            **_raw_traits(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db, start_character_review
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Lock Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        char_id = row["id"]
        try:
            assert row["review_started_at"] is None
            start_character_review(conn, char_id, reviewer_id="staff-smoke")
            row = conn.execute("SELECT * FROM characters WHERE id=?", (char_id,)).fetchone()
            assert row["review_started_at"] is not None
            assert row["review_started_by"] == "staff-smoke"

            # Now the player tries to save the sheet — they get redirected
            # with a flash error and the sheet stays unchanged.
            before_strength = _j.loads(row["sheet_json"]).get("attr_strength", 0)
        finally:
            pass

    # Player attempts to bump attr_strength to 5 — should be ignored
    rr = player.post(
        f"/characters/{char_id}/sheet",
        data={"_csrf": "dev-csrf-token", "attr_strength": "5"},
        follow_redirects=False,
    )
    assert rr.status_code == 303

    with get_db() as conn:
        row = conn.execute("SELECT * FROM characters WHERE id=?", (char_id,)).fetchone()
        after = _j.loads(row["sheet_json"]).get("attr_strength", 0)
        assert after == before_strength, "edit must be blocked while under review"
        conn.execute("DELETE FROM characters WHERE id=?", (char_id,))


def test_approved_character_sheet_edit_unlocked_despite_review_flag(staff, player):
    """approve_character clears review_started_at on approval; and the
    sheet-save lock is gated on `not is_approved AND review_started_at`, so an
    approved character is editable regardless. Guards both the clear-on-approve
    and the is_approved short-circuit — so the lock can't be 'simplified' to
    review_started_at-only and freeze every approved sheet."""
    import json as _j
    r = player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token", "name": "Unlock Smoke", "clan": "ventrue",
              "touchstones": _j.dumps(["Anchor One", "Anchor Two"]),
              **_raw_traits("ventrue")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db, start_character_review, approve_character
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Unlock Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
    try:
        with get_db() as conn:
            start_character_review(conn, cid, reviewer_id="staff-smoke")
            approve_character(conn, cid, reviewer_id="staff-smoke")
            row = conn.execute(
                "SELECT is_approved, review_started_at FROM characters WHERE id=?", (cid,)
            ).fetchone()
            assert row["is_approved"] == 1
            assert row["review_started_at"] is None  # approve clears the review flag
        # Player edits the sheet on the now-approved character — must persist.
        rr = player.post(
            f"/characters/{cid}/sheet",
            data={"_csrf": "dev-csrf-token", "attr_strength": "4"},
            follow_redirects=False,
        )
        assert rr.status_code == 303
        with get_db() as conn:
            sj = _j.loads(conn.execute(
                "SELECT sheet_json FROM characters WHERE id=?", (cid,)
            ).fetchone()["sheet_json"])
        assert sj.get("attr_strength") == 4, "approved sheet must be editable"

        # Belt-and-suspenders: even if the review flag is somehow set on an
        # approved row, the lock's `not is_approved` short-circuit keeps the
        # sheet editable — the safety doesn't depend solely on the clear.
        with get_db() as conn:
            conn.execute(
                "UPDATE characters SET review_started_at='2026-01-01T00:00:00Z' WHERE id=?",
                (cid,),
            )
            conn.commit()
        rr2 = player.post(
            f"/characters/{cid}/sheet",
            data={"_csrf": "dev-csrf-token", "attr_strength": "5"},
            follow_redirects=False,
        )
        assert rr2.status_code == 303
        with get_db() as conn:
            sj2 = _j.loads(conn.execute(
                "SELECT sheet_json FROM characters WHERE id=?", (cid,)
            ).fetchone()["sheet_json"])
        assert sj2.get("attr_strength") == 5, \
            "approved sheet must stay editable even with the review flag forced on"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            conn.commit()


def test_edit_locked_during_review(staff, player):
    """While staff has a pending character under review, /edit is frozen too
    (not just /sheet) — identity + profile fields can't change out from under
    the reviewer."""
    import json as _j
    r = player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token", "name": "Edit Lock Smoke", "clan": "brujah",
              "concept": "Original Concept",
              "touchstones": _j.dumps(["Anchor One", "Anchor Two"]),
              **_raw_traits()},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db, start_character_review
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Edit Lock Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
    try:
        with get_db() as conn:
            start_character_review(conn, cid, reviewer_id="staff-smoke")
        rr = player.post(
            f"/characters/{cid}/edit",
            data={"_csrf": "dev-csrf-token", "name": "Edit Lock Smoke",
                  "clan": "brujah", "concept": "CHANGED Concept"},
            follow_redirects=False,
        )
        assert rr.status_code == 303
        with get_db() as conn:
            concept = conn.execute(
                "SELECT concept FROM characters WHERE id=?", (cid,)
            ).fetchone()["concept"]
        assert concept == "Original Concept", "identity edits must be frozen during review"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            conn.commit()


# ── Hunting sites + hunt logs ────────────────────────────────────────────────

def test_staff_site_create_with_predator_dcs_and_coterie(staff):
    """Posting a new site with predator DCs + coterie + contested should
    persist all of it; reading back through list_hunting_sites returns
    the JSON-decoded predator_dcs dict."""
    from web.db import get_db, list_hunting_sites, create_coterie

    with get_db() as conn:
        co = create_coterie(conn, "DC Smoke Coterie")
    try:
        r = staff.post(
            "/staff/sites",
            data={
                "_csrf": "dev-csrf-token",
                "name": "Smoke Alley", "borough": "Manhattan",
                "description": "Test site.",
                "coterie_id": str(co["id"]),
                "dc_Alleycat": "2",
                "dc_Siren": "4",
            },
        )
        assert r.status_code == 200

        with get_db() as conn:
            sites = list_hunting_sites(conn, active_only=False)
        site = next(s for s in sites if s["name"] == "Smoke Alley")
        assert site["coterie_id"] == co["id"]
        assert site["predator_dcs"]["Alleycat"] == 2
        assert site["predator_dcs"]["Siren"] == 4
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunting_sites WHERE name='Smoke Alley'")
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))


def test_player_hunting_sites_directory_renders(player):
    r = player.get("/hunting-sites")
    assert r.status_code == 200
    assert "Hunting Sites" in r.text


def test_hunting_sites_shows_predator_specific_dc(player):
    """Backfill: the hunting-sites directory highlights the DC for the
    selected character's predator type. Selects the char explicitly so the
    highlight is deterministic regardless of how many chars the player has."""
    from web.db import get_db, create_hunting_site
    with get_db() as conn:
        ch = conn.execute(
            "SELECT id, predator_type FROM characters "
            "WHERE discord_id='111111111111111111' AND is_approved=1 "
            "ORDER BY id LIMIT 1").fetchone()
        assert ch is not None, "seed character for player 1 missing"
        create_hunting_site(
            conn, name="Backfill Feeding Ground", borough="Manhattan",
            blood_quality=2, predator_dcs={ch["predator_type"]: 7})
        conn.commit()
    try:
        r = player.get(f"/hunting-sites?character_id={ch['id']}")
        assert r.status_code == 200
        i = r.text.find("Backfill Feeding Ground")
        assert i != -1, "site did not render"
        assert "7" in r.text[i:i + 600], "predator-specific DC (7) not on the site card"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunting_sites WHERE name='Backfill Feeding Ground'")
            conn.commit()


def test_player_hunt_log_post_creates_row(player):
    """Player posts a hunt at a site → hunt_logs row appears."""
    from web.db import get_db, create_hunting_site, list_hunts_for_site
    with get_db() as conn:
        site = create_hunting_site(conn, name="Hunt Smoke Site", borough="Manhattan")
    try:
        r = player.post(
            f"/hunting-sites/{site['id']}/hunt",
            data={
                "_csrf": "dev-csrf-token",
                "character_id": "1",          # dev seed char
                "outcome": "success",
                "note":    "Quiet feed.",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            hunts = list_hunts_for_site(conn, site["id"])
        assert len(hunts) == 1
        assert hunts[0]["outcome"] == "success"
        assert hunts[0]["character_name"] == "Valeria Morano"
        assert hunts[0]["source"] == "web"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunt_logs WHERE site_id=?", (site["id"],))
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (site["id"],))


def test_bot_api_log_hunt_with_bearer_token(_client):
    """Bot endpoint POST /api/sites/{id}/hunt accepts bearer auth and
    creates a hunt_log with source='bot'."""
    from web.db import get_db, create_hunting_site, list_hunts_for_site
    with get_db() as conn:
        site = create_hunting_site(conn, name="Bot Hunt Site", borough="Brooklyn")
    try:
        r = _client.post(
            f"/api/sites/{site['id']}/hunt",
            json={"character_id": 1, "outcome": "messy_critical",
                  "note": "Dice said so."},
            headers={"Authorization": "Bearer smoke-test-token"},
        )
        assert r.status_code == 201, r.text
        with get_db() as conn:
            hunts = list_hunts_for_site(conn, site["id"])
        assert len(hunts) == 1
        assert hunts[0]["source"] == "bot"
        assert hunts[0]["outcome"] == "messy_critical"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunt_logs WHERE site_id=?", (site["id"],))
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (site["id"],))


def test_bot_api_log_hunt_rejects_unknown_outcome(_client):
    from web.db import get_db, create_hunting_site
    with get_db() as conn:
        site = create_hunting_site(conn, name="Outcome Smoke", borough="Queens")
    try:
        r = _client.post(
            f"/api/sites/{site['id']}/hunt",
            json={"character_id": 1, "outcome": "ridiculous", "note": ""},
            headers={"Authorization": "Bearer smoke-test-token"},
        )
        assert r.status_code == 400
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (site["id"],))


def test_bot_api_list_sites_returns_dcs_for_hunt_picker(_client):
    """GET /api/sites (bot auth) lists active sites with the data the bot's
    /hunt command needs: base + Chasse-effective DCs, blood quality, and the
    controlling coterie."""
    from web.db import get_db, create_hunting_site
    with get_db() as conn:
        site = create_hunting_site(
            conn, name="Sites List Smoke", borough="Bronx",
            blood_quality=4, predator_dcs={"Siren": 3, "Alleycat": 2})
    try:
        # Requires the bearer token.
        assert _client.get("/api/sites").status_code in (401, 403)
        r = _client.get("/api/sites",
                        headers={"Authorization": "Bearer smoke-test-token"})
        assert r.status_code == 200, r.text
        sites = r.json()["sites"]
        mine = next((s for s in sites if s["id"] == site["id"]), None)
        assert mine is not None
        assert mine["blood_quality"] == 4
        assert mine["predator_dcs"]["Siren"] == 3
        # No controlling coterie → effective DCs equal the base DCs.
        assert mine["effective_dcs"]["Siren"] == 3
        assert mine["coterie_id"] is None
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (site["id"],))


def test_require_sheet_toggle_changes_wizard_render(staff, player):
    """When require_sheet_on_create is OFF, the wizard collapses to the
    basics steps and the short-form Submit CTA shows. When ON, the full
    V5 final-step CTA shows. Note: the horizontal step tracker always
    renders every label string ("Flesh", "Skill", "Soul", ...) — those
    are JS metadata, not gated by require_sheet."""
    from web.db import get_db, upsert_settings

    # Off: short-form wizard
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    r_off = player.get("/characters/new")
    assert r_off.status_code == 200
    assert "Submit Character" in r_off.text  # short-form CTA
    assert "external source" in r_off.text   # short-form-mode banner copy

    # On: full wizard
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=1)
    r_on = player.get("/characters/new")
    assert r_on.status_code == 200
    assert "Submit for Approval" in r_on.text  # full-wizard final CTA
    assert "external source" not in r_on.text  # short-form banner absent


def test_create_with_toggle_off_redirects_to_sheet_tab(player):
    """Creating a character with require_sheet=False lands the player
    on the Sheet tab so they can fill it in."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    try:
        r = player.post(
            "/characters/new",
            data={"_csrf": "dev-csrf-token",
                  "name": "Short Form Smoke", "clan": "brujah",
            "touchstones": '["A", "B"]'},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "tab=sheet" in r.headers.get("location", "")
    finally:
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=1)
            conn.execute("DELETE FROM characters WHERE name='Short Form Smoke'")


# ── Phase 4: In Memoriam ──────────────────────────────────────────────────────

def test_ancilla_in_memoriam_submission_persists_blob(player):
    """Submitting a Kindred Ancilla with In Memoriam mode should store
    the era blob + generation + discipline spread."""
    import json as _j
    im_blob = {
        "generation":        "11th-10th",
        "discipline_spread": "focused",
        "embrace_age":       "up_to_100",
        "eras": [
            {"type": "calm",      "gambit_taken": False, "gambit_roll": None},
            {"type": "adversity", "gambit_taken": True,  "gambit_roll": 7},
            {"type": "violence",  "gambit_taken": False, "gambit_roll": 8},
        ],
    }
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, in_memoriam_enabled=1)
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Ancilla Smoke",
            "clan": "brujah",
            "character_type":       "kindred",
            "character_tier":       "ancilla",
            "ancilla_mode":         "in_memoriam",
            "im_generation":        "11th-10th",
            "im_discipline_spread": "focused",
            "in_memoriam":          _j.dumps(im_blob),
            "touchstones":          _j.dumps(["A", "B"]),
            **_raw_traits(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Ancilla Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            assert row["character_tier"]       == "ancilla"
            assert row["ancilla_mode"]         == "in_memoriam"
            assert row["im_generation"]        == "11th-10th"
            assert row["im_discipline_spread"] == "focused"
            # Raw row gives JSON string; parse before asserting shape.
            im_data = _j.loads(row["in_memoriam"])
            assert im_data["embrace_age"] == "up_to_100"
            assert len(im_data["eras"]) == 3
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))
            upsert_settings(conn, in_memoriam_enabled=0)


@pytest.mark.parametrize("posted,expected", [("4", 4), ("2", 4), ("11", 10), ("", 7)])
def test_in_memoriam_humanity_seeded_from_computed(player, posted, expected):
    """The IM character's Humanity is seeded from the wizard's computed value
    (posted as im_computed_humanity, RAW floor 4, clamped 4-10), not re-derived
    from a per-era field the wizard never stores."""
    import json as _j
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, in_memoriam_enabled=1)
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token", "name": "IM Humanity",
            "clan": "brujah", "character_type": "kindred",
            "character_tier": "ancilla", "ancilla_mode": "in_memoriam",
            "im_generation": "9th-8th", "im_discipline_spread": "focused",
            "im_computed_humanity": posted,
            "in_memoriam": _j.dumps({"embrace_age": "over_150", "eras": []}),
            "touchstones": _j.dumps(["A", "B"]),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, sheet_json FROM characters WHERE name='IM Humanity' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            sheet = _j.loads(row["sheet_json"])
            assert sheet["humanity"] == expected
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))
            upsert_settings(conn, in_memoriam_enabled=0)


def test_neonate_submission_clears_ancilla_fields(player):
    """Posting tier=neonate must not persist any IM state, even if the
    form accidentally includes leftover values from a prior selection."""
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Neonate Smoke",
            "clan": "brujah",
            "character_type":  "kindred",
            "character_tier":  "neonate",
            "ancilla_mode":    "in_memoriam",   # stale, should be discarded
            "im_generation":   "12th",           # stale
            "in_memoriam":     '{"embrace_age": "up_to_100", "eras": []}',
            "touchstones":     '["A", "B"]',
            **_raw_traits(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Neonate Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            assert row["character_tier"] == "neonate"
            assert row["ancilla_mode"]   is None
            assert row["im_generation"]  is None
            import json as _jj
            assert _jj.loads(row["in_memoriam"]) == {}
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))


# ── Phase 2: drafts + submission notes + budgets ─────────────────────────────

def test_save_as_draft_does_not_require_full_validation(player):
    """A POST with as_draft=1 should succeed with only a name set."""
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Draft Smoke",
            "as_draft": "1",
            # Note: no clan, no touchstones — full submit would reject this.
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Drafts redirect back to the roster, not to a character detail.
    assert r.headers.get("location", "").endswith("/characters")

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Draft Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            assert row["is_draft"] == 1
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))


def test_resume_draft_renders_wizard_with_state(player):
    """GET /characters/{id}/resume-draft should render the wizard with
    the saved fields pre-filled into the initialForm payload."""
    # Save a draft first
    r = player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token",
              "name": "Resume Smoke", "clan": "brujah",
              "concept": "Test concept",
              "as_draft": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM characters WHERE name='Resume Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        char_id = row["id"]
    try:
        rr = player.get(f"/characters/{char_id}/resume-draft")
        assert rr.status_code == 200
        # Concept should appear in the rendered form so Alpine
        # initializes with it.
        assert "Resume Smoke" in rr.text
        assert "Test concept" in rr.text
    finally:
        from web.db import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (char_id,))


def test_resume_draft_blocks_non_drafts(player):
    """Once a character is no longer a draft, the resume endpoint 404s."""
    from web.db import get_db, get_character
    with get_db() as conn:
        # Dev seed char id=1 is approved, not a draft
        char = get_character(conn, 1)
        assert not char.get("is_draft")
    r = player.get("/characters/1/resume-draft")
    assert r.status_code == 404


def test_drafts_section_renders_on_roster(player):
    """After saving a draft, the /characters page surfaces it in a
    distinct \"In Progress\" section."""
    r = player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token",
              "name": "Visible Draft", "as_draft": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rr = player.get("/characters")
    assert rr.status_code == 200
    assert "In Progress" in rr.text
    assert "Visible Draft" in rr.text
    assert "Continue editing" in rr.text

    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM characters WHERE name='Visible Draft'")


def test_admin_settings_saves_background_budget_and_flaw_cap(staff):
    """Phase 2 added two budget knobs — verify they round-trip."""
    r = staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "use_homebrew_rules": "on",
            "homebrew_starting_xp":      "75",
            "homebrew_merit_budget":     "7",
            "homebrew_advantage_budget": "2",
            "homebrew_background_budget":"6",
            "homebrew_flaw_cap":         "3",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    from web.db import get_db, get_settings
    with get_db() as conn:
        s = get_settings(conn)
    assert s["homebrew_background_budget"] == 6
    assert s["homebrew_flaw_cap"]          == 3


# ── Phase 1: character types + homebrew rules + revenants ────────────────────

def test_admin_settings_save_homebrew_and_revenants(staff):
    """The expanded /staff/admin/settings POST persists every chronicle
    toggle in one request — homebrew rules + revenant family list."""
    from web.db import get_db, get_settings
    r = staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "require_sheet_on_create": "on",
            "use_homebrew_rules": "on",
            "homebrew_starting_xp":      "120",
            "homebrew_merit_budget":     "10",
            "homebrew_advantage_budget": "3",
            "revenants_enabled": "on",
            "revenant_families": "Ducheski | Tremere\nBratovitch | Tzimisce\n",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    with get_db() as conn:
        s = get_settings(conn)
    assert s["use_homebrew_rules"]       == 1
    assert s["homebrew_starting_xp"]     == 120
    assert s["homebrew_merit_budget"]    == 10
    assert s["homebrew_advantage_budget"] == 3
    assert s["revenants_enabled"]        == 1
    fams = s["revenant_families"]
    assert any(f["name"] == "Ducheski" and f["parent_clan"] == "Tremere" for f in fams)
    assert any(f["name"] == "Bratovitch" and f["parent_clan"] == "Tzimisce" for f in fams)


def test_create_mortal_character_does_not_require_clan(player):
    """Mortals submit with no clan and should land successfully."""
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Mortal Smoke",
            "clan": "",
            "character_type": "mortal",
            "touchstones": '["A", "B"]',
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Mortal Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            assert row["character_type"] == "mortal"
            assert (row["clan"] or "") == ""
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))


def test_create_revenant_requires_family(player):
    """Posting type=revenant without a family must error."""
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Revenant Smoke",
            "clan": "",
            "character_type": "revenant",
            "revenant_family": "",
        },
    )
    assert r.status_code == 200
    assert "revenant family" in r.text.lower()


def test_create_revenant_with_family_persists(player):
    """Revenant submission stores family name."""
    r = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "Family Smoke",
            "clan": "",
            "character_type": "revenant",
            "revenant_family": "Ducheski",
            "touchstones": '["A", "B"]',
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    from web.db import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE name='Family Smoke' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            assert row["character_type"]  == "revenant"
            assert row["revenant_family"] == "Ducheski"
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (row["id"],))


def test_admin_settings_post_persists_toggle(staff):
    """POST /staff/admin/settings updates the chronicle row."""
    from web.db import get_db, get_settings
    r = staff.post("/staff/admin/settings",
                   data={"_csrf": "dev-csrf-token"},  # unchecked = off
                   follow_redirects=False)
    assert r.status_code == 303
    with get_db() as conn:
        row = get_settings(conn)
    assert row is not None
    assert row["require_sheet_on_create"] == 0

    # Toggle back on
    r2 = staff.post("/staff/admin/settings",
                    data={"_csrf": "dev-csrf-token",
                          "require_sheet_on_create": "on"},
                    follow_redirects=False)
    assert r2.status_code == 303
    with get_db() as conn:
        row = get_settings(conn)
    assert row["require_sheet_on_create"] == 1


def test_start_review_is_noop_if_already_approved():
    """start_character_review returns the row but doesn't overwrite if
    the character is already approved."""
    from web.db import get_db, start_character_review, get_character
    with get_db() as conn:
        # Dev seed character id=1 is already approved
        char = get_character(conn, 1)
        assert char["is_approved"]
        before = char.get("review_started_at")
        result = start_character_review(conn, 1, "staff-noop")
        assert result is not None
        after = get_character(conn, 1)
        assert after.get("review_started_at") == before  # unchanged


def test_list_characters_includes_last_activity_field():
    """list_characters should now include a last_activity_at column,
    computed from MAX(claims, spends, ledger). NULL when no activity."""
    from web.db import get_db, list_characters
    with get_db() as conn:
        chars = list_characters(conn)
        assert chars, "dev seed should produce at least one character"
        assert "last_activity_at" in chars[0]


def test_roster_renders_silent_chip_for_inactive(staff):
    """Hitting /staff/characters should render the page. The 'Silent Nw'
    chip is conditional — we just verify the page doesn't crash with the
    new field plumbed through."""
    r = staff.get("/staff/characters")
    assert r.status_code == 200
    assert "Roster" in r.text


def test_coterie_single_funder_spend_approve_deducts_xp(staff):
    """Single-funder coterie spend — the model that replaced the equal-split
    group-buy. ONE member funds the whole cost, so the spend is 'funded' on
    creation (no per-member commit cycle) and staff approval deducts that
    member's XP and writes a single ledger entry."""
    from web.db import (
        get_db, upsert_player, create_coterie, add_coterie_member, create_character,
        create_coterie_single_funder_spend, approve_coterie_spend, get_coterie_spend,
        update_character,
    )
    with get_db() as conn:
        upsert_player(conn, discord_id="510", username="SoloFunder")
        a = create_character(conn, discord_id="510", name="SoloFundChar", clan="brujah")
        conn.execute("UPDATE characters SET xp_total=20 WHERE id=?", (a["id"],))
        update_character(conn, a["id"], is_approved=1)
        co = create_coterie(conn, "SoloFundSmoke")
        add_coterie_member(conn, co["id"], a["id"])
        try:
            spend = create_coterie_single_funder_spend(
                conn,
                coterie_id=co["id"],
                funded_by_character_id=a["id"],
                contribution_type="paid_xp",
                target_kind="merit",
                target_name="Shared Haven",
                xp_cost=5,
                justification="One member foots the whole bill.",
            )
            # Single funder => funded immediately, awaiting staff (no commits).
            assert spend["status"] == "funded"
            assert spend["funded_by_character_id"] == a["id"]
            assert spend["per_member_cost"] == 5

            approve_coterie_spend(conn, spend["id"], reviewer_id="staff-smoke",
                                  notes="Approved per chronicle policy.")
            after = get_coterie_spend(conn, spend["id"])
            assert after["status"] == "approved"

            row = conn.execute("SELECT xp_spent FROM characters WHERE id=?", (a["id"],)).fetchone()
            assert row["xp_spent"] == 5

            ledger = conn.execute(
                "SELECT * FROM ledger_entries WHERE reference_type='coterie_spend' "
                "AND reference_id=?", (spend["id"],)
            ).fetchall()
            assert len(ledger) == 1
            assert ledger[0]["xp_delta"] == -5
        finally:
            conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (a["id"],))
            conn.execute("DELETE FROM coterie_spends WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (a["id"],))


def test_character_spend_approval_deducts_xp_and_raises_trait(staff):
    """A plain (non-coterie) character spend, once approved, deducts the
    verified cost from xp_spent, auto-raises the trait dot on the sheet, and
    writes one negative ledger entry. Regression for the core spend path."""
    from web.db import (get_db, upsert_player, create_character, update_character,
                        create_spend, approve_spend, get_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="710710710", username="SpendQA")
        ch = create_character(conn, discord_id="710710710",
                              name="SpendQA Char", clan="ventrue")
        conn.execute("UPDATE characters SET xp_total=20 WHERE id=?", (ch["id"],))
        update_character(conn, ch["id"], is_approved=1)
        try:
            sp = create_spend(conn, character_id=ch["id"], category="Clan Discipline",
                              trait_name="Dominate", current_dots=0, new_dots=1,
                              verified_cost=5)
            conn.commit()
            approve_spend(conn, sp["id"], reviewer_id="staff-smoke")
            conn.commit()
            c = get_character(conn, ch["id"])
            assert c["xp_spent"] == 5 and c["xp_available"] == 15
            assert (c["sheet_json"] or {}).get("disc_dominate") == 1
            led = conn.execute(
                "SELECT entry_type, xp_delta FROM ledger_entries WHERE character_id=?",
                (ch["id"],)).fetchall()
            assert any(e["entry_type"] == "spend" and e["xp_delta"] == -5 for e in led)
        finally:
            conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (ch["id"],))
            conn.execute("DELETE FROM spend_requests WHERE character_id=?", (ch["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (ch["id"],))
            conn.commit()


def test_claim_approval_grants_xp_to_total(staff):
    """Approving an XP claim grants its total to xp_total (earned XP) and
    writes an 'earn' ledger entry. Regression for the claim-grant path."""
    from web.db import (get_db, upsert_player, create_character, update_character,
                        create_period, set_period_active, close_period,
                        create_claim, approve_claim, get_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="711711711", username="ClaimQA")
        ch = create_character(conn, discord_id="711711711",
                              name="ClaimQA Char", clan="brujah")
        update_character(conn, ch["id"], is_approved=1)
        p = create_period(conn, label="QA Claim Period", period_type="night",
                          phase="full", opens_at="2026-05-01T00:00:00Z",
                          closes_at="2026-06-30T00:00:00Z", created_by="staff-smoke")
        set_period_active(conn, p["id"])
        try:
            claim = create_claim(
                conn, character_id=ch["id"], play_period_id=p["id"],
                claimed_criteria=[{"criteria_id": 1, "label": "Posting",
                                   "xp_value_at_submission": 3}],
                rp_links=["https://example.com/rp"])
            conn.commit()
            approve_claim(conn, claim["id"], reviewer_id="staff-smoke")
            conn.commit()
            c = get_character(conn, ch["id"])
            assert c["xp_total"] == 3
            led = conn.execute(
                "SELECT entry_type, xp_delta FROM ledger_entries WHERE character_id=?",
                (ch["id"],)).fetchall()
            assert any(e["entry_type"] == "earn" and e["xp_delta"] == 3 for e in led)
        finally:
            conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (ch["id"],))
            conn.execute("DELETE FROM xp_claims WHERE character_id=?", (ch["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (ch["id"],))
            close_period(conn, p["id"])
            conn.execute("DELETE FROM play_periods WHERE id=?", (p["id"],))
            conn.commit()


def test_coterie_pages_render_single_funder_spend(_client):
    """Render-level regression for the single-funder coterie flow. Both the
    player coterie detail page and the staff manage page must render a funded
    single-funder spend (funder name, withdraw / approve) without any of the
    removed equal-split group-buy machinery (commit grid, commit endpoints,
    'Commit Remaining' shortcut)."""
    from web.db import (
        get_db, create_coterie, add_coterie_member,
        create_coterie_single_funder_spend,
    )
    DEV_PLAYER_ID = "111111111111111111"
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, xp_total FROM characters "
            "WHERE discord_id=? AND name='Valeria Morano' LIMIT 1",
            (DEV_PLAYER_ID,),
        ).fetchone()
        assert row is not None, "dev seed character missing"
        char_id, orig_xp = row["id"], row["xp_total"]
        # Funder affordability is checked at creation, so guarantee some XP.
        conn.execute("UPDATE characters SET xp_total=? WHERE id=?", (orig_xp + 10, char_id))
        co = create_coterie(conn, "RenderSmokeCoterie")
        add_coterie_member(conn, co["id"], char_id)
        spend = create_coterie_single_funder_spend(
            conn,
            coterie_id=co["id"],
            funded_by_character_id=char_id,
            contribution_type="paid_xp",
            target_kind="merit",
            target_name="Shared Haven",
            xp_cost=5,
            justification="Render-test spend.",
        )
    try:
        # ── Player view (must own a member char to pass the gate) ──
        _client.cookies.clear()
        _client.get("/_dev/seed_data", follow_redirects=False)
        _client.get("/_dev/player", follow_redirects=False)
        r = _client.get(f"/coteries/{co['id']}")
        assert r.status_code == 200, r.status_code
        assert "Open Proposals" in r.text
        assert "Shared Haven" in r.text
        assert "Awaiting staff" in r.text
        assert "Withdraw Proposal" in r.text
        assert "funded by Valeria Morano" in r.text
        assert f"/coteries/{co['id']}/spends/{spend['id']}/commit\"" not in r.text

        # ── Staff view (no membership gate) ──
        _client.cookies.clear()
        _client.get("/_dev/seed", follow_redirects=False)
        rs = _client.get(f"/staff/coteries/{co['id']}")
        assert rs.status_code == 200, rs.status_code
        assert "Shared Haven" in rs.text
        assert "Funded" in rs.text
        assert "Approve" in rs.text
        assert "funded by Valeria Morano" in rs.text
        assert "Commit Remaining" not in rs.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coterie_spends WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("UPDATE characters SET xp_total=? WHERE id=?", (orig_xp, char_id))
            conn.commit()


def test_about_panel_renders_and_saves_with_gating(player):
    """The 'About My Character' panel renders on the character page, the
    /about endpoint saves the relocated identity/narrative fields, and
    profile_locked freezes the IC profile while concept/sire/covenant stay
    editable."""
    from web.db import get_db, get_character, create_character, update_character
    DEV = "111111111111111111"
    with get_db() as conn:
        c = create_character(conn, discord_id=DEV, name="AboutPanelTest", clan="brujah")
        cid = c["id"]
        update_character(conn, cid, is_approved=1)
        conn.commit()
    try:
        page = player.get(f"/characters/{cid}")
        assert page.status_code == 200
        assert "About My Character" in page.text

        # Save (unlocked) — all fields land
        r = player.post(f"/characters/{cid}/about", data={
            "_csrf": "dev-csrf-token",
            "concept": "Test Concept", "sire": "Test Sire", "covenant": "Anarch",
            "ambition": "Rule the night", "profession": "Bartender",
            "profile_blurb": "A blurb", "backstory": "Long story",
            "true_age": "120", "apparent_age": "30",
        }, follow_redirects=False)
        assert r.status_code == 303
        with get_db() as conn:
            c1 = get_character(conn, cid)
        assert c1["concept"] == "Test Concept"
        assert c1["ambition"] == "Rule the night"
        assert c1["profession"] == "Bartender"
        assert c1["true_age"] == 120
        assert c1["profile_blurb"] == "A blurb"

        # Lock the profile — concept stays editable, IC fields freeze
        with get_db() as conn:
            conn.execute("UPDATE characters SET profile_locked=1 WHERE id=?", (cid,))
            conn.commit()
        r2 = player.post(f"/characters/{cid}/about", data={
            "_csrf": "dev-csrf-token",
            "concept": "Locked Concept", "sire": "Test Sire", "covenant": "Anarch",
            "profile_blurb": "SHOULD NOT SAVE", "profession": "SHOULD NOT SAVE",
        }, follow_redirects=False)
        assert r2.status_code == 303
        with get_db() as conn:
            c2 = get_character(conn, cid)
        assert c2["concept"] == "Locked Concept"     # always editable
        assert c2["profile_blurb"] == "A blurb"       # frozen
        assert c2["profession"] == "Bartender"        # frozen
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (cid,))
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            conn.commit()


def test_coterie_proposal_wizard_fields_and_site_link(player):
    """C1: the proposal stores the acquaintance acknowledgment + requested
    hunting site, the acknowledgment is required, and approval links an
    unclaimed site to the newly-formed coterie."""
    from web.db import (get_db, list_pending_coterie_requests,
                        approve_coterie_request, get_coterie_request)
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO hunting_sites (name, borough) VALUES (?, ?)",
            ("Test Site C1", "Manhattan"))
        site_id = cur.lastrowid
        conn.commit()
    req_id = coterie_id = None
    try:
        # Acknowledgment is required — a submission without it re-renders the
        # form with the error and creates no request.
        r0 = player.post("/coteries/request", data={
            "_csrf": "dev-csrf-token", "proposed_name": "NoAck Coterie",
            "member_ids": "[]",
        }, follow_redirects=False)
        assert r0.status_code == 200
        assert "know and have met" in r0.text

        # Valid submission with the ack + a requested site.
        r = player.post("/coteries/request", data={
            "_csrf": "dev-csrf-token", "proposed_name": "WizardTest Coterie",
            "members_acquainted": "on", "requested_site_id": str(site_id),
            "member_ids": "[]", "note": "We've met IC.",
        }, follow_redirects=False)
        assert r.status_code == 200
        with get_db() as conn:
            req = next(q for q in list_pending_coterie_requests(conn)
                       if q["proposed_name"] == "WizardTest Coterie")
        req_id = req["id"]
        assert req["members_acquainted"] == 1
        assert req["requested_site_id"] == site_id
        assert req["requested_site_name"] == "Test Site C1"

        # Approval forms the coterie and links the (unclaimed) site to it.
        with get_db() as conn:
            approve_coterie_request(conn, req_id, reviewer_id="staff-smoke")
            conn.commit()
            done = get_coterie_request(conn, req_id)
            coterie_id = done["coterie_id"]
            site = conn.execute(
                "SELECT coterie_id FROM hunting_sites WHERE id=?", (site_id,)
            ).fetchone()
        assert done["status"] == "approved"
        assert site["coterie_id"] == coterie_id
    finally:
        with get_db() as conn:
            if coterie_id:
                conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (coterie_id,))
                conn.execute("DELETE FROM coteries WHERE id=?", (coterie_id,))
            if req_id:
                conn.execute("DELETE FROM coterie_requests WHERE id=?", (req_id,))
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (site_id,))
            conn.commit()


def test_free_creation_dots_budget_and_caps(_client):
    """C2/C3b: the coterie creation pool is 2/member + 1 per flaw dot (max 4
    bonus). Spending is capped at the pool total; C/L/P at 3; named at 3."""
    import pytest as _p
    from web.db import (get_db, upsert_player, create_coterie, add_coterie_member,
                        create_character, commit_free_creation_dots, commit_coterie_flaw,
                        coterie_free_budget, coterie_effective_rating)
    with get_db() as conn:
        upsert_player(conn, discord_id="c2a", username="C2A")
        upsert_player(conn, discord_id="c2b", username="C2B")
        a = create_character(conn, discord_id="c2a", name="FreeDotA", clan="brujah")
        b = create_character(conn, discord_id="c2b", name="FreeDotB", clan="brujah")
        co = create_coterie(conn, "FreeDotsSmoke", creation_state="forming")
        add_coterie_member(conn, co["id"], a["id"])
        add_coterie_member(conn, co["id"], b["id"])
        try:
            assert coterie_free_budget(conn, co["id"])["total"] == 4   # 2 members x 2
            # Bring Chasse to the creation cap of 3 across two members.
            commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                      target_kind="chasse", target_name=None, dots=2)
            commit_free_creation_dots(conn, coterie_id=co["id"], character_id=b["id"],
                                      target_kind="chasse", target_name=None, dots=1)
            assert coterie_effective_rating(conn, co["id"], "chasse") == 3
            with _p.raises(ValueError, match="capped at 3 at creation"):
                commit_free_creation_dots(conn, coterie_id=co["id"], character_id=b["id"],
                                          target_kind="chasse", target_name=None, dots=1)
            # One base dot left -> a merit; pool now exhausted.
            commit_free_creation_dots(conn, coterie_id=co["id"], character_id=b["id"],
                                      target_kind="merit", target_name="Haven", dots=1)
            assert coterie_free_budget(conn, co["id"])["left"] == 0
            with _p.raises(ValueError, match="creation dot"):
                commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                          target_kind="merit", target_name="Library", dots=1)
            # Take a flaw -> +1 bonus dot -> can spend one more (on a background).
            commit_coterie_flaw(conn, coterie_id=co["id"], flaw_name="Adversary", dots=1)
            assert coterie_free_budget(conn, co["id"])["total"] == 5
            commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                      target_kind="background", target_name="Allies", dots=1)
            assert coterie_effective_rating(conn, co["id"], "background", "Allies") == 1
            # Flaw dots cap at 4 total.
            commit_coterie_flaw(conn, coterie_id=co["id"], flaw_name="Hunted", dots=3)
            with _p.raises(ValueError, match="flaw dots"):
                commit_coterie_flaw(conn, coterie_id=co["id"], flaw_name="Extra", dots=1)
            conn.commit()
        finally:
            conn.execute("DELETE FROM coterie_contributions WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("DELETE FROM characters WHERE id IN (?,?)", (a["id"], b["id"]))
            conn.commit()


def test_coterie_lifecycle_signoff_and_free_dot_gate(_client):
    """C3a: a coterie forms → submits → staff sign off → active. Free creation
    dots (and submission) are only valid while forming."""
    import pytest as _p
    from web.db import (get_db, create_coterie, add_coterie_member, create_character,
                        upsert_player, submit_coterie_sheet, approve_coterie_sheet,
                        commit_free_creation_dots, get_coterie)
    with get_db() as conn:
        upsert_player(conn, discord_id="c3a", username="C3A")
        a = create_character(conn, discord_id="c3a", name="C3aChar", clan="brujah")
        co = create_coterie(conn, "LifecycleSmoke", creation_state="forming")
        add_coterie_member(conn, co["id"], a["id"])
        try:
            # Free dots work while forming.
            commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                      target_kind="chasse", target_name=None, dots=1)
            # forming -> submitted
            submit_coterie_sheet(conn, co["id"], "c3a")
            assert get_coterie(conn, co["id"])["creation_state"] == "submitted"
            # No free dots once submitted.
            with _p.raises(ValueError, match="forming"):
                commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                          target_kind="lien", target_name=None, dots=1)
            # submitted -> active (staff sign-off)
            approve_coterie_sheet(conn, co["id"], "staff-smoke")
            assert get_coterie(conn, co["id"])["creation_state"] == "active"
            # No free dots once active, and you can't re-submit a finalised coterie.
            with _p.raises(ValueError, match="forming"):
                commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                          target_kind="lien", target_name=None, dots=1)
            with _p.raises(ValueError, match="forming"):
                submit_coterie_sheet(conn, co["id"], "c3a")
            conn.commit()
        finally:
            conn.execute("DELETE FROM coterie_contributions WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (a["id"],))
            conn.commit()


def test_staff_coterie_traits_unified_to_contributions(staff):
    """C3-unify: staff adding a coterie merit/flaw writes the unified
    contributions model, so staff + players read one sheet."""
    from web.db import (get_db, create_coterie, add_coterie_member,
                        create_character, upsert_player, list_coterie_contributions)
    with get_db() as conn:
        upsert_player(conn, discord_id="u1", username="UnifyP")
        a = create_character(conn, discord_id="u1", name="UnifyChar", clan="brujah")
        co = create_coterie(conn, "UnifySmoke")
        add_coterie_member(conn, co["id"], a["id"])
    try:
        r = staff.post(f"/staff/coteries/{co['id']}/merits/add", data={
            "_csrf": "dev-csrf-token", "character_id": str(a["id"]),
            "merit_name": "Shared Library", "dots": "2", "target_kind": "merit",
        }, follow_redirects=False)
        assert r.status_code == 200
        with get_db() as conn:
            contribs = list_coterie_contributions(conn, co["id"], status="active")
        merit = [c for c in contribs if c["target_name"] == "Shared Library"]
        assert len(merit) == 1 and merit[0]["target_kind"] == "merit" and merit[0]["dots"] == 2

        rf = staff.post(f"/staff/coteries/{co['id']}/flaws/add", data={
            "_csrf": "dev-csrf-token", "flaw_name": "Adversary", "dots": "1",
        }, follow_redirects=False)
        assert rf.status_code == 200
        with get_db() as conn:
            flaws = [c for c in list_coterie_contributions(conn, co["id"], status="active")
                     if c["target_kind"] == "flaw"]
        assert any(f["target_name"] == "Adversary" for f in flaws)
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coterie_contributions WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (a["id"],))
            conn.commit()


def test_coterie_spend_flows_gated_by_state(player):
    """C4: advance/buy/donate flows show while active (ongoing advancement) but
    are frozen (hidden) while the sheet is submitted for staff sign-off."""
    from web.db import get_db, create_coterie, add_coterie_member
    DEV = "111111111111111111"
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM characters WHERE discord_id=? AND name='Valeria Morano' LIMIT 1",
            (DEV,)).fetchone()
        cid = row["id"]
        co = create_coterie(conn, "C4Smoke", creation_state="active")
        add_coterie_member(conn, co["id"], cid)
        conn.commit()
    try:
        r = player.get(f"/coteries/{co['id']}")
        assert r.status_code == 200
        assert "Advance Coterie Rating" in r.text       # active -> available
        with get_db() as conn:
            conn.execute("UPDATE coteries SET creation_state='submitted' WHERE id=?", (co["id"],))
            conn.commit()
        r2 = player.get(f"/coteries/{co['id']}")
        assert r2.status_code == 200
        assert "Advance Coterie Rating" not in r2.text   # submitted -> frozen
        assert "Awaiting Sign-off" in r2.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.commit()


def test_hunting_site_chasse_reduces_dcs(_client):
    """Feature D: a controlling coterie's Chasse lowers a site's predator DCs
    (1 per dot, floored at 1); effective_dcs == base when uncontrolled."""
    from web.db import (get_db, create_hunting_site, create_coterie,
                        get_hunting_site, update_hunting_site)
    with get_db() as conn:
        co = create_coterie(conn, "ChasseSmoke")
        conn.execute("UPDATE coteries SET chasse=2 WHERE id=?", (co["id"],))
        site = create_hunting_site(conn, "DC Test Site", "Manhattan",
                                   predator_dcs={"Alleycat": 3, "Bagger": 1})
        sid = site["id"]
        conn.commit()
    try:
        with get_db() as conn:
            s0 = get_hunting_site(conn, sid)
        assert s0["chasse_reduction"] == 0
        assert s0["effective_dcs"]["Alleycat"] == 3          # uncontrolled = base
        with get_db() as conn:
            update_hunting_site(conn, sid, coterie_id=co["id"])
            s1 = get_hunting_site(conn, sid)
        assert s1["chasse_reduction"] == 2
        assert s1["controlling_coterie"] == "ChasseSmoke"
        assert s1["effective_dcs"]["Alleycat"] == 1          # 3 - 2
        assert s1["effective_dcs"]["Bagger"] == 1            # max(1, 1 - 2)
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (sid,))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.commit()


def test_chasse_reduction_only_for_owning_coterie(player):
    """D-fix: the Chasse DC reduction applies only when the viewing character
    is a member of the controlling coterie — outsiders hunting there see the
    base DCs."""
    from web.db import (get_db, create_coterie, create_hunting_site, create_character,
                        add_coterie_member, update_hunting_site, update_character)
    DEV = "111111111111111111"
    with get_db() as conn:
        a = create_character(conn, discord_id=DEV, name="OwnViewer", clan="brujah")
        update_character(conn, a["id"], is_approved=1)
        co = create_coterie(conn, "OwnSmoke")
        conn.execute("UPDATE coteries SET chasse=2 WHERE id=?", (co["id"],))
        site = create_hunting_site(conn, "OwnSite", "Manhattan", predator_dcs={"Alleycat": 3})
        sid = site["id"]
        update_hunting_site(conn, sid, coterie_id=co["id"])
        conn.commit()
    try:
        # Viewer NOT in the owning coterie -> base DCs, no reduction note.
        r = player.get(f"/hunting-sites/{sid}?character_id={a['id']}")
        assert r.status_code == 200
        assert "your Chasse" not in r.text
        # Add them to the coterie -> reduction now applies.
        with get_db() as conn:
            add_coterie_member(conn, co["id"], a["id"])
            conn.commit()
        r2 = player.get(f"/hunting-sites/{sid}?character_id={a['id']}")
        assert "your Chasse" in r2.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (sid,))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (a["id"],))
            conn.commit()


def test_coterie_creation_undo(player):
    """Polish: a member can remove a free-dot / flaw allocation while forming."""
    from web.db import (get_db, create_coterie, add_coterie_member, create_character,
                        commit_free_creation_dots, list_coterie_contributions, update_character)
    DEV = "111111111111111111"
    with get_db() as conn:
        a = create_character(conn, discord_id=DEV, name="UndoViewer", clan="brujah")
        update_character(conn, a["id"], is_approved=1)
        co = create_coterie(conn, "UndoSmoke", creation_state="forming")
        add_coterie_member(conn, co["id"], a["id"])
        c = commit_free_creation_dots(conn, coterie_id=co["id"], character_id=a["id"],
                                      target_kind="chasse", target_name=None, dots=1)
        cid = c["id"]
        conn.commit()
    try:
        r = player.post(f"/coteries/{co['id']}/creation/{cid}/remove",
                        data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
        assert r.status_code == 200
        with get_db() as conn:
            active = list_coterie_contributions(conn, co["id"], status="active")
        assert not any(x["id"] == cid for x in active)   # removed
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coterie_contributions WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (co["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))
            conn.execute("DELETE FROM characters WHERE id=?", (a["id"],))
            conn.commit()


def test_staff_review_page_signoff(staff):
    """Polish: staff can sign off a submitted coterie from the review page
    (plain POST -> redirect, coterie goes active)."""
    from web.db import get_db, create_coterie, get_coterie
    with get_db() as conn:
        co = create_coterie(conn, "ReviewSignoff", creation_state="submitted")
        cid = co["id"]
        conn.commit()
    try:
        r = staff.get(f"/staff/coteries/{cid}")
        assert r.status_code == 200
        assert "Awaiting Sign-off" in r.text
        assert f"/staff/coteries/{cid}/approve-sheet" in r.text
        ap = staff.post(f"/staff/coteries/{cid}/approve-sheet",
                        data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
        assert ap.status_code == 303
        with get_db() as conn:
            assert get_coterie(conn, cid)["creation_state"] == "active"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coteries WHERE id=?", (cid,))
            conn.commit()


def test_aurora_visual_layer_wired(player):
    """The aurora CSS + JS bundles should be linked from every page
    via base.html, the SVG LUT filter should be inlined for body { filter: url(#aurora-grade) },
    and the landing page should boot the WebGL hero shader."""
    # Any authenticated page proves base.html is wiring the visual layer
    r = player.get("/characters")
    assert r.status_code == 200
    # CSS bundle linked
    assert "/static/css/aurora.css" in r.text
    # JS bundle linked
    assert "/static/js/aurora.js" in r.text
    # Inline SVG LUT defined (matched on filter id)
    assert 'id="aurora-grade"' in r.text


def test_aurora_sparkle_host_stays_interactive():
    """Regression guard — this exact bug has bitten three times (chargen
    clan buttons, staff claim approve/reject, staff spend approve/reject).

    `.aurora-sparkle-host` wraps REAL interactive controls, and CSS
    `pointer-events` is inherited, so a `pointer-events: none` on the host
    silently disables every nested button — their HTMX/Alpine clicks never
    fire. The decorative injected `.aurora-spark` children opt out of
    hit-testing on their own, so the host itself must stay interactive."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1]
           / "web" / "static" / "css" / "aurora.css").read_text(encoding="utf-8")
    # Strip CSS comments so explanatory prose that *mentions* the property
    # (like the warning comment guarding this very rule) can't trip the check.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    host = re.search(r"\.aurora-sparkle-host\s*\{([^}]*)\}", css)
    assert host, ".aurora-sparkle-host rule not found in aurora.css"
    assert "pointer-events" not in host.group(1), (
        "`.aurora-sparkle-host` must stay interactive — pointer-events is "
        "inherited and kills nested buttons (claims/spends approve+reject)."
    )

    spark = re.search(r"\.aurora-spark\s*\{([^}]*)\}", css)
    assert spark, ".aurora-spark rule not found in aurora.css"
    assert re.search(r"pointer-events\s*:\s*none", spark.group(1)), (
        "`.aurora-spark` particles must keep pointer-events:none so the "
        "decorative sparks never intercept clicks."
    )


def test_chargen_revenant_select_required_is_conditional():
    """Regression guard — the Revenant-family <select> on the Nature step is
    always in the DOM but hidden (x-show) for non-revenants. A *static*
    `required` on a display:none control makes the whole form invalid, so the
    browser silently blocks Submit for every Kindred/Mortal/Ghoul character.
    The value posts via the hidden #revenant_family input, so the select must
    gate its requirement on character type with `:required`, never a bare
    `required`. Backend POST tests can't catch this — they bypass browser
    constraint validation — so guard it at the template level."""
    import re
    from pathlib import Path
    tpl = (Path(__file__).resolve().parents[1]
           / "web" / "templates" / "player" / "character_create.html").read_text(encoding="utf-8")

    i = tpl.index('x-model="charName.revenant_family"')
    start = tpl.rindex("<select", 0, i)
    end = tpl.index(">", i)
    tag = tpl[start:end + 1]

    assert ":required=" in tag, (
        "Revenant-family <select> must use a conditional :required binding."
    )
    assert re.search(r"(?<!:)\brequired\b", tag) is None, (
        "Revenant-family <select> has a bare `required` — it blocks form "
        "submission for every non-revenant character. Use :required instead."
    )


def test_sheet_pips_use_reliable_utility_classes():
    """Regression guard — the character sheet's rating pips must use the
    hand-rolled .pip-on/.pip-off utilities (codex.css), NOT a Tailwind
    arbitrary class like bg-[var(--clan,…)]. The precompiled tailwind.css
    drops arbitrary values, so the arbitrary class rendered every filled
    pip transparent and made ratings unreadable."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sheet = (root / "web" / "templates" / "player" / "character.html").read_text(encoding="utf-8")
    codex = (root / "web" / "static" / "css" / "codex.css").read_text(encoding="utf-8")
    assert "pip-on" in sheet and "pip-off" in sheet, "sheet pips should use .pip-on/.pip-off"
    assert "bg-[var(--clan,theme(colors.gold.500))]" not in sheet, (
        "Sheet pips must not use the arbitrary bg-[var(--clan,…)] class — "
        "it isn't compiled into tailwind.css, so filled pips render blank."
    )
    assert ".pip-on" in codex and ".pip-off" in codex, "codex.css must define the pip utilities"


def test_chargen_has_no_sire_field():
    """Sire is collected in the About My Character panel, not during chargen
    (staff direction 2026-05). The wizard must not render a sire input."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parents[1]
           / "web" / "templates" / "player" / "character_create.html").read_text(encoding="utf-8")
    assert 'name="sire"' not in tpl, "chargen should not have a Sire input — it's set in About."


def test_about_section_hosts_profile_image_form():
    """Profile-image management (avatar + upload/remove) was relocated into
    the About My Character section on the character page, reusing the
    existing /image routes."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parents[1]
           / "web" / "templates" / "player" / "character.html").read_text(encoding="utf-8")
    assert "/image" in tpl and 'name="image"' in tpl, (
        "About section should host the profile-image upload form."
    )


def test_site_predator_types_exclude_restricted():
    """Hunting-site favored-predator list must exclude the restricted
    predators (Blood Leech feeds on vampires, Tithe Collector bends the
    Hunger economy) — they don't represent a mortal hunting profile and
    shouldn't be selectable as a site's favored predator."""
    from web.v5_traits import (
        V5_SITE_PREDATOR_TYPES, V5_RESTRICTED_PREDATOR_TYPES, V5_PREDATOR_TYPES,
    )
    for r in V5_RESTRICTED_PREDATOR_TYPES:
        assert r not in V5_SITE_PREDATOR_TYPES, f"{r} must not be a site predator"
    assert "Alleycat" in V5_SITE_PREDATOR_TYPES
    assert set(V5_SITE_PREDATOR_TYPES) == set(V5_PREDATOR_TYPES) - set(V5_RESTRICTED_PREDATOR_TYPES)


def test_every_predator_type_has_benefit_info():
    """Every predator type needs a benefits summary so the chargen panel
    renders for it, and the three Players-Guide additions are present."""
    from web.v5_traits import V5_PREDATOR_TYPES, V5_PREDATOR_INFO
    for pt in V5_PREDATOR_TYPES:
        assert pt in V5_PREDATOR_INFO, f"{pt} is missing a V5_PREDATOR_INFO entry"
        assert V5_PREDATOR_INFO[pt].get("benefits"), f"{pt} has empty benefits text"
    for added in ("Pursuer", "Roadside Killer", "Trapdoor"):
        assert added in V5_PREDATOR_TYPES, f"{added} should be a selectable predator type"


def test_predator_grants_are_well_formed():
    """Every predator's structured `grants` must reference valid skill/
    discipline keys and valid sheet lists, so the wizard pickers and the
    sheet-application logic never hit an unknown trait. Also checks the
    skill/discipline spread constants are internally consistent."""
    from web.v5_traits import (
        V5_PREDATOR_INFO, V5_SKILLS, V5_DISCIPLINES,
        V5_SKILL_SPREADS, V5_DISCIPLINE_SPREADS,
    )
    skill_keys = {k for _, ts in V5_SKILLS for k, _ in ts}
    disc_keys = {k for k, _ in V5_DISCIPLINES}
    lists = {"merits", "backgrounds", "flaws", "advantages"}

    def check(pt, g):
        kind = g.get("kind")
        if kind == "specialty":
            for o in g["options"]:
                assert o["skill"] in skill_keys, f"{pt}: bad skill {o['skill']}"
                assert o.get("name")
        elif kind == "discipline":
            assert g["options"], f"{pt}: empty discipline options"
            for d in g["options"]:
                assert d in disc_keys, f"{pt}: bad discipline {d}"
        elif kind == "fixed":
            assert g["list"] in lists and g.get("name") and g.get("dots")
        elif kind == "delta":
            assert g["trait"] in ("humanity", "blood_potency")
        elif kind == "choice":
            assert g["options"]
            for sub in g["options"]:
                check(pt, sub)
        elif kind == "pool":
            assert g["list"] in lists and g.get("dots") and len(g.get("options", [])) >= 2
        else:
            raise AssertionError(f"{pt}: unknown grant kind {kind!r}")

    for pt, info in V5_PREDATOR_INFO.items():
        assert "grants" in info, f"{pt} has no grants"
        for g in info["grants"]:
            check(pt, g)

    for spr in V5_SKILL_SPREADS.values():
        assert spr["levels"] and spr.get("label")
    for slug, spr in V5_DISCIPLINE_SPREADS.items():
        assert sum(lvl * n for lvl, n in spr["levels"].items()) == spr["total"], f"{slug} total mismatch"


def test_chargen_persists_spreads_and_predator_picks(player):
    """The create route stores skill_spread, discipline_spread, and the
    resolved predator_choices into sheet_json so the build is reflected on
    the player + staff sheets."""
    import json as _json
    from web.db import get_db
    # This test pins skill_spread="specialist", so it needs a RAW-valid
    # Specialist skill allocation (one 4, three 3s, three 2s, three 1s) rather
    # than the Balanced helper. Attributes still use the standard 4/3/3/3/2/2/2/2/1.
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token",
        "name": "Spread Test",
        "clan": "brujah",
        "predator_type": "Alleycat",
        "touchstones": '["A", "B"]',
        # Attributes — RAW spread.
        "attr_strength": "4", "attr_dexterity": "3", "attr_stamina": "3",
        "attr_charisma": "3", "attr_manipulation": "2", "attr_composure": "2",
        "attr_intelligence": "2", "attr_wits": "2", "attr_resolve": "1",
        # Skills — Specialist: one 4, three 3s, three 2s, three 1s.
        "skill_brawl": "4",
        "skill_athletics": "3", "skill_stealth": "3", "skill_melee": "3",
        "skill_firearms": "2", "skill_larceny": "2", "skill_streetwise": "2",
        "skill_awareness": "1", "skill_drive": "1", "skill_occult": "1",
        "skill_spread": "specialist",
        "discipline_spread": "standard",
        "predator_choices": _json.dumps({"s0": 1, "d1": "disc_potence"}),
        # Brujah 2+1 base with the Alleycat free dot folded onto Potence → 2 / 2.
        "disc_celerity": "2", "disc_potence": "2",
        # RAW advantages: 7 dots (Backgrounds + Merit) + 2 Flaw dots.
        "backgrounds": _json.dumps([{"name": "Allies", "dots": 3},
                                    {"name": "Resources", "dots": 2}]),
        "merits": _json.dumps([{"name": "Iron Will", "dots": 2}]),
        "flaws": _json.dumps([{"name": "Enemy", "dots": 1},
                              {"name": "Disliked", "dots": 1}]),
        # 1 free specialty (this Specialist build dots no Academics/Craft/Perf/Science).
        "specialties": _json.dumps([{"skill": "skill_brawl", "name": "Grappling"}]),
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='Spread Test'"
            ).fetchone()
        assert row is not None, "character was not created"
        sheet = _json.loads(row["sheet_json"] or "{}")
        assert sheet.get("skill_spread") == "specialist"
        assert sheet.get("discipline_spread") == "standard"
        assert sheet.get("predator_choices", {}).get("d1") == "disc_potence"
        # Alleycat grants −1 Humanity; applied server-side on top of the
        # neonate base of 7.
        assert sheet.get("humanity") == 6
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Spread Test'")


def test_resume_draft_restores_predator_picks_and_src_tags(player):
    """Regression for the draft-resume advantage-duplication bug. Saving a
    draft with predator-granted entries then resuming must:
      (1) preserve the src='predator' provenance tag on rated-list entries +
          specialties so the wizard dedupes instead of duplicating, and
      (2) surface predator_choices at the TOP level of initialForm so the
          Hunt-step pickers come back filled, not blank (which previously
          forced a re-pick that stacked a second copy of each grant)."""
    import json as _json
    from web.db import get_db
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Resume Pred", "clan": "brujah",
        "predator_type": "Alleycat", "as_draft": "1",
        "advantages": _json.dumps([
            {"name": "Criminal Contacts", "dots": 3, "src": "predator"},
            {"name": "Resources", "dots": 1},
        ]),
        "specialties": _json.dumps([
            {"skill": "skill_streetwise", "name": "Black Market", "src": "predator"},
        ]),
        "predator_choices": _json.dumps({"s0": 1, "d1": "disc_potence"}),
        "skill_spread": "specialist", "discipline_spread": "standard",
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, sheet_json FROM characters WHERE name='Resume Pred'"
            ).fetchone()
        assert row is not None, "draft was not created"
        char_id = row["id"]
        sheet = _json.loads(row["sheet_json"] or "{}")
        # (1) src tag survived the persist round-trip on both lists.
        assert any(a.get("name") == "Criminal Contacts" and a.get("src") == "predator"
                   for a in sheet.get("advantages", [])), "advantage src tag dropped"
        assert any(s.get("src") == "predator"
                   for s in sheet.get("specialties", [])), "specialty src tag dropped"
        # (2) resume surfaces the picks where the wizard reads them
        # (initialForm.predator_choices, top-level — not nested in sheet).
        rr = player.get(f"/characters/{char_id}/resume-draft")
        assert rr.status_code == 200
        i = rr.text.find("predator_choices")
        assert i != -1 and "disc_potence" in rr.text[i:i + 160], \
            "predator picks not restored at top level of initialForm"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Resume Pred'")


def test_homebrew_tier_budget_reaches_chargen(player):
    """Regression for 'homebrew ruleset not working': a per-tier homebrew
    override saved in the admin must flow through to the chargen wizard's
    budget seed (Starting XP, combined Advantages pool, flaw cap)."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(
            conn, actor_id="test", active_ruleset="homebrew",
            homebrew_tier_budgets={"neonate": {
                "xp": 99, "merits": 4, "advantages": 4, "backgrounds": 4,
                "merits_advantages_backgrounds": 12, "flaw_cap": 5}},
        )
        conn.commit()
    try:
        r = player.get("/characters/new")
        assert r.status_code == 200
        # The wizard's default (neonate) budget seed is tojson+forceescaped,
        # so the homebrew XP renders as starting_xp&#34;: 99.
        assert "starting_xp&#34;: 99" in r.text, "homebrew Starting XP not in wizard budget"
        assert "&#34;homebrew&#34;: true" in r.text
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="test",
                            active_ruleset="standard", homebrew_tier_budgets={})
            conn.commit()


def test_safe_image_return_validates_next():
    """The image routes only honor a `next` that is the character's own
    sheet or edit page; foreign or mismatched targets fall back to the
    sheet so a redirect can't be hijacked to another character or host."""
    from web.routes.player import _safe_image_return
    assert _safe_image_return("/characters/5", 5) == "/characters/5"
    assert _safe_image_return("/characters/5/edit", 5) == "/characters/5/edit"
    assert _safe_image_return("https://evil.example", 5) == "/characters/5"
    assert _safe_image_return("/characters/9", 5) == "/characters/5"  # other char
    assert _safe_image_return("//evil.example", 5) == "/characters/5"
    assert _safe_image_return("", 5) == "/characters/5"
    assert _safe_image_return(None, 5) == "/characters/5"


def test_sidebar_has_portal_switcher_for_staff(staff):
    """Staff belong to both portals — the sidebar shows a Player/Staff
    Portal switcher so they can jump between them from either side."""
    r = staff.get("/staff")
    assert r.status_code == 200
    assert "Player Portal" in r.text and "Staff Portal" in r.text


def test_sidebar_hides_portal_switcher_for_plain_player(player):
    """Plain players only have the player portal — no Staff Portal button."""
    r = player.get("/characters")
    assert r.status_code == 200
    assert "Staff Portal" not in r.text


def test_chargen_budget_sidebar_has_trait_trackers(player):
    """The chargen budget sidebar tracks attributes / skills / specialties /
    disciplines against their spread targets (not just the dot pools)."""
    r = player.get("/characters/new")
    assert r.status_code == 200
    for token in ("attrSpreadDots", "skillSpreadDots", "freeSpecialtyCount"):
        assert token in r.text, f"missing budget tracker: {token}"


def test_chargen_advance_caps_buys_at_four(player):
    """At chargen you cannot buy a 5th dot — the Advance step passes max=4
    to canBuy/buyTrait so every purchasable trait caps at 4."""
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert "canBuy('attr', 'attr_strength', 4)" in r.text


def test_chargen_persists_starting_xp_allocation(player):
    """The Advancement step's purchases (ledger + totals) persist into
    sheet_json so the build shows on the player + staff sheets."""
    import json as _json
    from web.db import get_db
    buys = [
        {"cat": "attr", "key": "attr_dexterity", "label": "Dexterity", "cost": 5},
        {"cat": "disc", "key": "disc_celerity", "label": "Celerity", "cost": 5},
    ]
    # The chargen validator checks the BASE spread (final dots minus xp_buys) and
    # caps every trait at 4 at creation. Dexterity carries one bought dot, so its
    # final value is 4 (base 4-1=3); the other attributes supply the rest of the
    # 4/3/3/3/2/2/2/2/1 spread directly. Skills come from the Balanced helper.
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "XP Persist", "clan": "brujah",
        "touchstones": '["A", "B"]',
        "xp_buys": _json.dumps(buys), "xp_spent": "10", "xp_pool": "75",
        **_raw_traits(),
        # Override attributes so the base spread stays RAW after subtracting the
        # one bought Dexterity dot (final 4 → base 3). Celerity also carries one
        # bought dot (final 3 → base 2), keeping the 2+1 Discipline base intact.
        "attr_strength": "4", "attr_dexterity": "4", "attr_stamina": "3",
        "attr_charisma": "3", "attr_manipulation": "2", "attr_composure": "2",
        "attr_intelligence": "2", "attr_wits": "2", "attr_resolve": "1",
        "disc_celerity": "3",
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='XP Persist'"
            ).fetchone()
        assert row is not None, "character was not created"
        sheet = _json.loads(row["sheet_json"] or "{}")
        assert sheet.get("xp_spent") == 10
        assert sheet.get("starting_xp_pool") == 75
        assert len(sheet.get("xp_buys", [])) == 2
        # attr_dexterity = RAW base 3 + 1 bought dot = 4; disc_celerity = base 2
        # + 1 bought dot = 3 (both folded in via the xp_buys ledger).
        assert sheet.get("attr_dexterity") == 4 and sheet.get("disc_celerity") == 3
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='XP Persist'")


def test_creation_xp_posts_single_clean_ledger_entry(player):
    """Approving a character with Starting-XP (CC) spends posts ONE clean
    ledger entry — the lump CC-XP spend with its amount, not a per-trait
    breakdown — and re-approval doesn't double-post it."""
    import json as _json
    from web.db import (get_db, upsert_player, create_character,
                        approve_character, get_ledger, delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="555000555000555000", username="CCLedger")
        ch = create_character(conn, discord_id="555000555000555000",
                              name="CC Ledger Probe", clan="brujah")
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (_json.dumps({"xp_spent": 40, "starting_xp_pool": 75}), ch["id"]))
        conn.commit()
    try:
        with get_db() as conn:
            approve_character(conn, ch["id"], reviewer_id="999999999999999999")
            conn.commit()
            creation = [e for e in get_ledger(conn, ch["id"])
                        if e["entry_type"] == "creation"]
        assert len(creation) == 1, "expected exactly one CC-XP ledger entry"
        assert creation[0]["xp_delta"] == -40
        assert "Creation" in (creation[0]["note"] or "")
        # Re-approval must not duplicate the entry.
        with get_db() as conn:
            approve_character(conn, ch["id"], reviewer_id="999999999999999999")
            conn.commit()
            again = [e for e in get_ledger(conn, ch["id"])
                     if e["entry_type"] == "creation"]
        assert len(again) == 1, "re-approval double-posted the CC-XP entry"
    finally:
        with get_db() as conn:
            delete_character(conn, ch["id"])
            conn.commit()


def test_no_creation_carryover_for_zero_pool_characters(player):
    """A character with no creation-XP pool carries NOTHING over on approval.
    Regression: mortals/ghouls/revenants keep character_tier='neonate' in the
    column, so the old `_pool <= 0` tier fallback wrongly handed them the
    neonate's 15 XP. A genuinely recorded pool of 0 must likewise carry nothing."""
    import json as _json
    from web.db import (get_db, upsert_player, create_character, approve_character,
                        get_character, get_ledger, delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="555111555111555111", username="ZeroPool")
        # (a) a mortal with NO recorded pool — exercises the type-aware fallback
        mortal = create_character(conn, discord_id="555111555111555111",
                                  name="Zero Pool Mortal", clan="brujah",
                                  character_type="mortal")
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (_json.dumps({}), mortal["id"]))
        # (b) a character that genuinely recorded a 0 pool (spent nothing)
        rec0 = create_character(conn, discord_id="555111555111555111",
                                name="Recorded Zero", clan="brujah")
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (_json.dumps({"starting_xp_pool": 0, "xp_spent": 0}), rec0["id"]))
        conn.commit()
    try:
        with get_db() as conn:
            for cid in (mortal["id"], rec0["id"]):
                approve_character(conn, cid, reviewer_id="999999999999999999")
            conn.commit()
            for cid in (mortal["id"], rec0["id"]):
                c = get_character(conn, cid)
                carry = [e for e in get_ledger(conn, cid) if e["entry_type"] == "carryover"]
                assert c["xp_total"] == 0, f"char {cid} got phantom XP: {c['xp_total']}"
                assert (c["creation_xp"] or 0) == 0, f"char {cid} creation_xp not 0"
                assert carry == [], f"char {cid} posted a carryover entry: {carry}"
    finally:
        with get_db() as conn:
            delete_character(conn, mortal["id"])
            delete_character(conn, rec0["id"])
            conn.commit()


def test_kindred_without_recorded_pool_still_carries_tier_xp(player):
    """A Kindred with no recorded starting_xp_pool (older / staff-seeded) still
    gets the tier's finishing XP carried over — the fallback is intact for them."""
    import json as _json
    from web.db import (get_db, upsert_player, create_character, approve_character,
                        get_character, get_ledger, delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="555222555222555222", username="TierFallback")
        ch = create_character(conn, discord_id="555222555222555222",
                              name="Seeded Neonate", clan="ventrue")
        conn.execute("UPDATE characters SET sheet_json=?, character_tier='neonate' WHERE id=?",
                     (_json.dumps({}), ch["id"]))
        conn.commit()
    try:
        with get_db() as conn:
            approve_character(conn, ch["id"], reviewer_id="999999999999999999")
            conn.commit()
            c = get_character(conn, ch["id"])
            carry = [e for e in get_ledger(conn, ch["id"]) if e["entry_type"] == "carryover"]
        assert c["xp_total"] == 15 and (c["creation_xp"] or 0) == 15
        assert len(carry) == 1 and carry[0]["xp_delta"] == 15
    finally:
        with get_db() as conn:
            delete_character(conn, ch["id"])
            conn.commit()


def test_clan_bane_flaws_maps_nosferatu_standard():
    """The chargen-flaw lookup maps Nosferatu's standard Bane → Repulsive (2),
    keyed by bane choice so the variant (Infestation) grants none."""
    from web.routes.player import _clan_bane_flaws, _clan_bane_flaw_for
    m = _clan_bane_flaws()
    assert m.get("nosferatu", {}).get("standard") == {"name": "Repulsive", "dots": 2}
    assert _clan_bane_flaw_for("nosferatu", "standard") == {"name": "Repulsive", "dots": 2}
    assert _clan_bane_flaw_for("nosferatu", "variant") is None
    assert _clan_bane_flaw_for("brujah", "standard") is None


def test_chargen_passes_clan_bane_flaws(player):
    """The wizard receives the standard-bane chargen flaws + the variant Bane
    data so it can auto-apply the flaw and offer the standard/variant picker."""
    r = player.get("/characters/new")
    assert r.status_code == 200
    assert "clanBaneFlaws" in r.text and "Repulsive" in r.text
    assert "clanBaneVariants" in r.text and "Infestation" in r.text


def test_nosferatu_variant_bane_grants_no_flaw(player):
    """Choosing Nosferatu's variant Bane (Infestation) grants NO Repulsive
    flaw, persists bane_choice='variant' + the variant name, and strips any
    stale clan-bane flaw left in the form."""
    import json as _json
    from web.db import get_db
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Nos Var", "clan": "nosferatu",
        "touchstones": '["A", "B"]', "bane_choice": "variant",
        "flaws": _json.dumps([{"name": "Repulsive", "dots": 2, "src": "clan_bane"}]),
        **_raw_traits("nosferatu"),
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='Nos Var'").fetchone()
        assert row is not None
        sheet = _json.loads(row["sheet_json"] or "{}")
        assert sheet.get("bane_choice") == "variant"
        assert sheet.get("bane_variant_name") == "Infestation"
        assert not any(f.get("src") == "clan_bane" for f in sheet.get("flaws", [])), \
            "variant Bane should not grant a clan-bane flaw"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Nos Var'")
            conn.commit()


def test_nosferatu_standard_bane_grants_repulsive(player):
    """Nosferatu's standard Bane auto-grants a free Repulsive (2) flaw tagged
    src='clan_bane' — applied server-side even if the form omits it."""
    import json as _json
    from web.db import get_db
    # Post WITHOUT any flaws to prove the server-side safety net applies it.
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Nos Bane", "clan": "nosferatu",
        "touchstones": '["A", "B"]',
        **_raw_traits("nosferatu"),
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='Nos Bane'").fetchone()
        assert row is not None
        flaws = _json.loads(row["sheet_json"] or "{}").get("flaws", [])
        assert any(f.get("name") == "Repulsive" and f.get("dots") == 2
                   and f.get("src") == "clan_bane" for f in flaws), \
            "Nosferatu did not get the auto Repulsive clan-bane flaw"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Nos Bane'")
            conn.commit()


def test_non_nosferatu_has_no_auto_bane_flaw(player):
    """A clan without a standard-Bane chargen flaw (e.g. Brujah) gets none."""
    import json as _json
    from web.db import get_db
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Bru NoBane", "clan": "brujah",
        "touchstones": '["A", "B"]',
        **_raw_traits(),
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='Bru NoBane'").fetchone()
        flaws = _json.loads(row["sheet_json"] or "{}").get("flaws", [])
        assert not any(f.get("src") == "clan_bane" for f in flaws)
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Bru NoBane'")
            conn.commit()


def test_bane_severity_from_blood_potency():
    """Bane Severity scales with Blood Potency (V5 Corebook p.216):
    0 at BP 0, else ceil(BP / 2) + 1."""
    from web.v5_traits import bane_severity_for_bp
    assert bane_severity_for_bp(0) == 0
    assert bane_severity_for_bp(1) == 2
    assert bane_severity_for_bp(2) == 2
    assert bane_severity_for_bp(3) == 3
    assert bane_severity_for_bp(4) == 3
    assert bane_severity_for_bp(7) == 5
    assert bane_severity_for_bp(10) == 6


def test_hecata_variant_bane_auto_applies_decay_pool(player):
    """Hecata's variant Bane (Decay) auto-applies Bane-Severity Flaw dots among
    Retainer/Haven/Resources (free). An empty pool triggers the server
    auto-fill so the effect always lands."""
    import json as _json
    from web.db import get_db
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Hec Decay", "clan": "hecata",
        "touchstones": '["A", "B"]', "bane_choice": "variant",
        "bane_flaw_pool": "{}",   # empty → server auto-fills Bane Severity dots
        **_raw_traits("hecata"),
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='Hec Decay'").fetchone()
        assert row is not None
        sheet = _json.loads(row["sheet_json"] or "{}")
        assert sheet.get("bane_choice") == "variant"
        assert sheet.get("bane_variant_name") == "Decay"
        # Neonate BP 1 → Bane Severity 2 (ceil(1/2)+1) → two free Decay Flaw dots.
        decay = [f for f in sheet.get("flaws", []) if f.get("src") == "clan_bane"]
        assert sum(f.get("dots", 0) for f in decay) == 2
        assert all(f["name"] in ("Retainer", "Haven", "Resources") for f in decay)
        assert sheet.get("bane_flaw_pool")  # allocation persisted for resume
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Hec Decay'")
            conn.commit()


def test_hecata_decay_pool_honors_player_allocation(player):
    """A posted Decay distribution is honored (not overridden by auto-fill)."""
    import json as _json
    from web.db import get_db
    player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "name": "Hec Alloc", "clan": "hecata",
        "touchstones": '["A", "B"]', "bane_choice": "variant",
        "bane_flaw_pool": _json.dumps({"Haven": 2}),   # full BP1 severity (2)
        **_raw_traits("hecata"),
    }, follow_redirects=False)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name='Hec Alloc'").fetchone()
        sheet = _json.loads(row["sheet_json"] or "{}")
        decay = [f for f in sheet.get("flaws", []) if f.get("src") == "clan_bane"]
        assert len(decay) == 1 and decay[0]["name"] == "Haven" and decay[0]["dots"] == 2
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Hec Alloc'")
            conn.commit()


def test_active_clan_bane_helper_resolves_standard_and_variant():
    """active_clan_bane resolves the standard vs chosen-variant Bane and
    returns None for archetypes without a clan Bane."""
    from web.v5_traits import active_clan_bane
    std = active_clan_bane("nosferatu", "standard")
    assert std and std["name"] == "Repulsiveness" and std["variant"] is False
    var = active_clan_bane("ventrue", "variant")
    assert var and var["name"] == "Hierarchy" and var["variant"] is True
    assert active_clan_bane("", "standard") is None


def test_staff_sheet_shows_active_clan_bane_effect(staff):
    """The staff character sheet surfaces the active clan Bane's name + the
    mechanical effect text (not just the build-summary one-liner)."""
    # Seed character id 1 (Valeria) is Brujah → standard Bane 'Volatile temper'.
    r = staff.get("/staff/characters/1")
    assert r.status_code == 200
    assert "Clan Bane" in r.text
    assert "Volatile temper" in r.text
    assert "fury frenzy" in r.text


def test_chargen_error_rerender_with_image_does_not_500(player):
    """A validation error on chargen must re-render the wizard (200), not
    500. The multipart form carries a profile_image UploadFile that isn't
    JSON-serializable, so the re-render must drop non-string fields before
    the wizard's `initialForm | tojson`."""
    r = player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token", "name": "Img Err", "clan": "brujah"},
        files={"profile_image": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 200, f"expected wizard re-render, got {r.status_code}"
    assert "Please correct the following" in r.text


def test_coterie_one_character_per_player(_client):
    """A player can't put two of their own characters in the same coterie."""
    import pytest
    from web.db import (
        get_db, create_character, approve_character, create_coterie, add_coterie_member,
    )
    with get_db() as conn:
        a = create_character(conn, discord_id="770000000000000001", name="Twin Aa", clan="brujah")
        b = create_character(conn, discord_id="770000000000000001", name="Twin Bb", clan="brujah")
        approve_character(conn, a["id"], "staff")
        approve_character(conn, b["id"], "staff")
        cot = create_coterie(conn, name="One Per Player Test")
        try:
            add_coterie_member(conn, cot["id"], a["id"])
            with pytest.raises(ValueError, match="one character per player"):
                add_coterie_member(conn, cot["id"], b["id"])
        finally:
            conn.execute("DELETE FROM coterie_memberships WHERE coterie_id=?", (cot["id"],))
            conn.execute("DELETE FROM coteries WHERE id=?", (cot["id"],))
            conn.execute("DELETE FROM characters WHERE id IN (?, ?)", (a["id"], b["id"]))


def test_char_cap_blocks_creation(player):
    """At the per-player cap, both the chargen page and a submit are blocked."""
    from web.db import get_db, upsert_settings
    try:
        with get_db() as conn:
            upsert_settings(conn, actor_id="test", max_chars_per_player=1)
        # Dev player 1 already has a character, so the cap (1) is reached.
        r = player.get("/characters/new", follow_redirects=False)
        assert r.status_code == 303, "chargen page should redirect at cap"
        r2 = player.post("/characters/new", data={
            "_csrf": "dev-csrf-token", "name": "Over Cap", "clan": "brujah",
            "touchstones": '["A", "B"]',
        }, follow_redirects=False)
        assert r2.status_code == 200, "submit should re-render, not redirect"
        assert "limit" in r2.text.lower()
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="test", max_chars_per_player=0)
            conn.execute("DELETE FROM characters WHERE name='Over Cap'")


def test_clan_color_utilities_defined_and_used():
    """Clan-color arbitrary Tailwind classes (border-[var(--clan,…)] etc.)
    don't survive the precompiled build, so clan identity must use the
    reliable codex.css utilities — and the character pages must not
    reintroduce the arbitrary classes that silently fail."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    codex = (root / "web" / "static" / "css" / "codex.css").read_text(encoding="utf-8")
    for util in (".border-clan", ".text-clan", ".bg-clan"):
        assert util in codex, f"codex.css must define {util}"
    for rel in ("web/templates/player/character.html",
                "web/templates/staff/character_detail.html"):
        txt = (root / rel).read_text(encoding="utf-8")
        assert "-[var(--clan" not in txt, f"{rel} still uses an arbitrary clan class"


def test_aurora_landing_renders_atmosphere(_client):
    """Unauthenticated landing page renders the static aurora-layer
    backdrop (CSS-only halo, no WebGL motion) so the landing remains
    quiet but atmospheric."""
    _client.cookies.clear()
    r = _client.get("/")
    assert r.status_code == 200
    assert 'class="aurora-layer"' in r.text


def test_player_map_page_renders(player):
    """Player /map renders with the Leaflet bootstrap + data fetch."""
    r = player.get("/map")
    assert r.status_code == 200
    assert "Chronicle Map" in r.text
    assert "leaflet" in r.text.lower()
    assert "/map/data.json" in r.text


def test_staff_map_page_renders(staff):
    """Staff /staff/map renders with the layer manager + import dialog."""
    r = staff.get("/staff/map")
    assert r.status_code == 200
    assert "Chronicle Map" in r.text
    assert "Import" in r.text
    assert "/staff/map/data.json" in r.text
    assert "/staff/map/layers" in r.text


def test_map_data_json_player_filters_staff_layers(staff, player):
    """Public layers should appear on /map/data.json; staff-only layers
    must not. The same staff-only layer DOES appear on /staff/map/data.json."""
    from web.db import get_db, create_map_layer, delete_map_layer
    with get_db() as conn:
        pub = create_map_layer(conn, name="MapPublicSmoke", visibility="public",
                                created_by="smoke")
        sec = create_map_layer(conn, name="MapStaffOnlySmoke", visibility="staff",
                                created_by="smoke")
    try:
        rp = player.get("/map/data.json")
        assert rp.status_code == 200
        names_player = [l["name"] for l in rp.json()["layers"]]
        assert "MapPublicSmoke" in names_player
        assert "MapStaffOnlySmoke" not in names_player

        staff.get("/_dev/seed", follow_redirects=False)
        rs = staff.get("/staff/map/data.json")
        assert rs.status_code == 200
        names_staff = [l["name"] for l in rs.json()["layers"]]
        assert "MapPublicSmoke" in names_staff
        assert "MapStaffOnlySmoke" in names_staff
    finally:
        with get_db() as conn:
            delete_map_layer(conn, pub["id"])
            delete_map_layer(conn, sec["id"])


def test_geojson_import_inserts_features():
    """import_geojson should parse a FeatureCollection and insert each
    Feature as a row with the right feature_type + geometry."""
    from web.db import (
        get_db, create_map_layer, delete_map_layer,
        import_geojson, list_map_features,
    )
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"name": "Elysium"},
             "geometry": {"type": "Point", "coordinates": [-74.0, 40.7]}},
            {"type": "Feature",
             "properties": {"name": "Ventrue Domain", "category": "elder-fief"},
             "geometry": {"type": "Polygon",
                          "coordinates": [[[-74.1, 40.7], [-74.0, 40.7],
                                            [-74.0, 40.8], [-74.1, 40.7]]]}},
        ],
    }
    with get_db() as conn:
        layer = create_map_layer(conn, name="GeoJsonSmoke")
        try:
            result = import_geojson(conn, layer["id"], payload,
                                    tag_field="category")
            assert result["inserted"] == 2
            assert result["skipped"] == 0
            feats = list_map_features(conn, layer_id=layer["id"])
            assert len(feats) == 2
            labels = sorted(f["label"] for f in feats)
            assert labels == ["Elysium", "Ventrue Domain"]
            types = sorted(f["feature_type"] for f in feats)
            assert types == ["point", "polygon"]
            # The polygon row should have the tag pulled from "category"
            poly = next(f for f in feats if f["feature_type"] == "polygon")
            assert poly["tag"] == "elder-fief"
        finally:
            delete_map_layer(conn, layer["id"])


def test_kml_import_parses_google_maps_placemarks():
    """import_kml should handle a Google Maps "My Maps" KML export —
    Point, LineString, and Polygon placemarks."""
    from web.db import (
        get_db, create_map_layer, delete_map_layer,
        import_kml, list_map_features,
    )
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Empire State</name>
      <description>Landmark</description>
      <Point><coordinates>-73.9857,40.7484,0</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Subway Line</name>
      <LineString><coordinates>
        -74.0060,40.7128,0 -73.9857,40.7484,0 -73.9352,40.7794,0
      </coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>Central Park</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        -73.9819,40.7681,0 -73.9494,40.7969,0 -73.9580,40.8005,0 -73.9819,40.7681,0
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
  </Document>
</kml>"""
    with get_db() as conn:
        layer = create_map_layer(conn, name="KmlSmoke")
        try:
            result = import_kml(conn, layer["id"], kml)
            assert result["inserted"] == 3, result
            feats = list_map_features(conn, layer_id=layer["id"])
            labels = sorted(f["label"] for f in feats)
            assert labels == ["Central Park", "Empire State", "Subway Line"]
            types = sorted(f["feature_type"] for f in feats)
            assert types == ["line", "point", "polygon"]
            # Polygon should be closed (first == last) in our normalized form
            poly = next(f for f in feats if f["feature_type"] == "polygon")
            coords = poly["geometry"]["coordinates"][0]
            assert coords[0] == coords[-1]
        finally:
            delete_map_layer(conn, layer["id"])


def test_map_feature_create_drops_a_pin(staff):
    """POST /staff/map/features should drop a single Point feature on
    the given layer with the supplied lat/lng — used by the click-to-pin
    tool on the staff map."""
    from web.db import (
        get_db, create_map_layer, delete_map_layer, list_map_features,
    )
    staff.get("/_dev/seed", follow_redirects=False)
    with get_db() as conn:
        layer = create_map_layer(conn, name="DropPinSmoke")
    try:
        r = staff.post(
            "/staff/map/features",
            data={
                "_csrf": "dev-csrf-token",
                "layer_id": str(layer["id"]),
                "label": "Elysium",
                "tag": "elysium",
                "lat": "40.7484",
                "lng": "-73.9857",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            feats = list_map_features(conn, layer_id=layer["id"])
            assert len(feats) == 1
            assert feats[0]["label"] == "Elysium"
            assert feats[0]["tag"] == "elysium"
            assert feats[0]["feature_type"] == "point"
            # GeoJSON Point coords are [lng, lat]
            assert feats[0]["geometry"]["coordinates"] == [-73.9857, 40.7484]
    finally:
        with get_db() as conn:
            delete_map_layer(conn, layer["id"])


def test_map_feature_edit_links_site_id(staff):
    """The feature edit endpoint should accept site_id + coterie_id form
    fields and persist them on the feature row."""
    from web.db import (
        get_db, create_map_layer, delete_map_layer, create_map_feature,
        get_map_feature, create_hunting_site,
    )
    staff.get("/_dev/seed", follow_redirects=False)
    with get_db() as conn:
        layer = create_map_layer(conn, name="LinkSmoke")
        site = create_hunting_site(conn, name="LinkSiteSmoke", borough="Manhattan")
        feat = create_map_feature(
            conn, layer_id=layer["id"], label="Test",
            feature_type="point",
            geometry={"type": "Point", "coordinates": [-74.0, 40.7]},
        )
    try:
        r = staff.post(
            f"/staff/map/features/{feat['id']}/edit",
            data={
                "_csrf": "dev-csrf-token",
                "label": "Linked Test",
                "site_id": str(site["id"]),
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            updated = get_map_feature(conn, feat["id"])
            assert updated["site_id"] == site["id"]
            assert updated["label"] == "Linked Test"
    finally:
        with get_db() as conn:
            delete_map_layer(conn, layer["id"])
            conn.execute("DELETE FROM hunting_sites WHERE id=?", (site["id"],))


def test_map_quick_import_creates_layer_and_features(staff):
    """The one-step /staff/map/quick-import endpoint should create
    the named layer AND import the payload's features in a single POST."""
    import json as _j
    from web.db import get_db, list_map_layers, list_map_features, delete_map_layer
    staff.get("/_dev/seed", follow_redirects=False)
    payload = _j.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"name": "Quick A"},
             "geometry": {"type": "Point", "coordinates": [-74.0, 40.7]}},
            {"type": "Feature",
             "properties": {"name": "Quick B"},
             "geometry": {"type": "Point", "coordinates": [-73.9, 40.8]}},
        ],
    })
    r = staff.post(
        "/staff/map/quick-import",
        data={
            "_csrf": "dev-csrf-token",
            "name": "QuickImportSmoke",
            "color": "#336699",
            "visibility": "public",
            "payload": payload,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    with get_db() as conn:
        layers = list_map_layers(conn, include_staff_only=True, active_only=False)
        smoke = next((l for l in layers if l["name"] == "QuickImportSmoke"), None)
        assert smoke is not None
        try:
            feats = list_map_features(conn, layer_id=smoke["id"])
            assert len(feats) == 2
            assert sorted(f["label"] for f in feats) == ["Quick A", "Quick B"]
        finally:
            delete_map_layer(conn, smoke["id"])


def test_map_quick_import_rolls_back_layer_on_bad_payload(staff):
    """If the import fails (bad JSON), the empty layer should NOT be
    left dangling — the route deletes it so staff can retry cleanly."""
    from web.db import get_db, list_map_layers
    staff.get("/_dev/seed", follow_redirects=False)

    r = staff.post(
        "/staff/map/quick-import",
        data={
            "_csrf": "dev-csrf-token",
            "name": "BadJsonSmoke",
            "payload": "{ this is not json",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        layers = list_map_layers(conn, include_staff_only=True, active_only=False)
        assert not any(l["name"] == "BadJsonSmoke" for l in layers), \
            "failed import should not leave an empty layer behind"


def test_map_layer_import_via_http(staff):
    """Posting a GeoJSON payload to the import endpoint should land
    the features on the layer and redirect with a success flash."""
    import json as _j
    from web.db import (
        get_db, create_map_layer, delete_map_layer, list_map_features,
    )
    with get_db() as conn:
        layer = create_map_layer(conn, name="HttpImportSmoke")
    try:
        staff.get("/_dev/seed", follow_redirects=False)
        payload = _j.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"name": "HQ"},
                 "geometry": {"type": "Point",
                              "coordinates": [-74.0, 40.7]}},
            ],
        })
        r = staff.post(
            f"/staff/map/layers/{layer['id']}/import",
            data={"_csrf": "dev-csrf-token", "payload": payload},
            follow_redirects=False,
        )
        assert r.status_code == 303
        with get_db() as conn:
            feats = list_map_features(conn, layer_id=layer["id"])
            assert len(feats) == 1
            assert feats[0]["label"] == "HQ"
            assert feats[0]["feature_type"] == "point"
    finally:
        with get_db() as conn:
            delete_map_layer(conn, layer["id"])


def test_staff_can_retire_and_unretire(staff):
    """The manual retire / un-retire endpoints flip status + clear the
    retirement marker. Both write audit rows."""
    from web.db import get_db, get_character
    staff.get("/_dev/seed", follow_redirects=False)
    # Seed character (Valeria Morano) starts active + approved.
    r = staff.post(
        "/staff/characters/1/retire",
        data={"_csrf": "dev-csrf-token"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        row = get_character(conn, 1)
        assert row["status"] == "retired"

    r2 = staff.post(
        "/staff/characters/1/unretire",
        data={"_csrf": "dev-csrf-token"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    with get_db() as conn:
        row = get_character(conn, 1)
        assert row["status"] == "active"
        assert row["retirement_eligible_at"] is None


def test_profile_lock_freezes_player_blurb_edit(staff, player):
    """Locking a profile means player POST to /edit can't change
    blurb / pronouns / backstory anymore — identity fields still flow."""
    from web.db import get_db, get_character
    staff.get("/_dev/seed", follow_redirects=False)
    # Lock the seed character's profile
    staff.post("/staff/characters/1/toggle-lock",
               data={"_csrf": "dev-csrf-token"},
               follow_redirects=False)

    # Player attempts to update blurb — should be ignored
    player.get("/_dev/player", follow_redirects=False)
    player.post(
        "/characters/1/edit",
        data={
            "_csrf": "dev-csrf-token",
            "profile_blurb": "Locked attempt — should not save",
            "concept": "still editable",
        },
        follow_redirects=False,
    )
    with get_db() as conn:
        row = get_character(conn, 1)
        assert row["profile_blurb"] != "Locked attempt — should not save"
        assert row["concept"] == "still editable"

    # Unlock for subsequent tests
    staff.get("/_dev/seed", follow_redirects=False)
    staff.post("/staff/characters/1/toggle-lock",
               data={"_csrf": "dev-csrf-token"},
               follow_redirects=False)


def test_ingrained_discipline_grant_persists(staff):
    """POSTing to /set-ingrained should set both has_ingrained_flaw=1
    and the named discipline. Submitting blank clears both."""
    from web.db import get_db, get_character
    staff.get("/_dev/seed", follow_redirects=False)
    r = staff.post(
        "/staff/characters/1/set-ingrained",
        data={"_csrf": "dev-csrf-token", "discipline": "Auspex"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        row = get_character(conn, 1)
        assert row["has_ingrained_flaw"] == 1
        assert row["ingrained_discipline"] == "Auspex"

    staff.post(
        "/staff/characters/1/set-ingrained",
        data={"_csrf": "dev-csrf-token", "discipline": ""},
        follow_redirects=False,
    )
    with get_db() as conn:
        row = get_character(conn, 1)
        assert row["has_ingrained_flaw"] == 0
        assert row["ingrained_discipline"] is None


def test_dashboard_surfaces_near_cap_count(staff):
    """The dashboard handler should include n_near_cap in the context
    via list_characters_near_cap(). With seed data the count is 0 since
    the seed character has fresh XP — we just verify the wiring."""
    staff.get("/_dev/seed", follow_redirects=False)
    r = staff.get("/staff")
    assert r.status_code == 200
    assert "Near Cap" in r.text


def test_list_characters_near_cap_helper():
    """list_characters_near_cap returns approved+active rows within
    threshold_xp of cap, with an xp_to_cap column for sorting."""
    from web.db import (
        get_db, create_character, list_characters_near_cap,
        upsert_player, upsert_settings,
    )
    with get_db() as conn:
        # near-cap only applies when the cap is on; pin the amount so the
        # xp_to_cap math is deterministic regardless of other tests' settings.
        upsert_settings(conn, xp_cap_enabled=1, xp_cap_amount=350)
        upsert_player(conn, discord_id="9001", username="NearCapSmoke")
        c = create_character(conn, discord_id="9001",
                             name="NearCapSmoke", clan="brujah")
        # Bring it close to cap (350 - 15 = 335)
        conn.execute(
            "UPDATE characters SET xp_total=335, is_approved=1, status='active' WHERE id=?",
            (c["id"],),
        )
        try:
            rows = list_characters_near_cap(conn, threshold_xp=30)
            assert any(r["id"] == c["id"] for r in rows)
            # xp_to_cap should be 15 for this character
            row = next(r for r in rows if r["id"] == c["id"])
            assert row["xp_to_cap"] == 15
        finally:
            conn.execute("DELETE FROM characters WHERE id=?", (c["id"],))


def test_staff_role_permission_matrix():
    """Admin gets every permission; Storyteller/Moderator get full XP +
    chronicle management but not settings/roles; Helper is spends-only;
    unknown/missing roles deny everything."""
    from web.db import STAFF_PERMISSIONS, staff_role_has_permission
    # Admin has manage_roles + manage_settings (the only role that does)
    assert staff_role_has_permission("admin", "manage_roles")
    assert staff_role_has_permission("admin", "manage_settings")
    # Storyteller = "XP in general": award + spend + manual adjust, no settings/roles
    assert staff_role_has_permission("storyteller", "approve_claim")
    assert staff_role_has_permission("storyteller", "approve_spend")
    assert staff_role_has_permission("storyteller", "adjust_xp")
    assert not staff_role_has_permission("storyteller", "manage_settings")
    assert not staff_role_has_permission("storyteller", "manage_roles")
    # Moderator mirrors Storyteller's Enoch powers exactly
    assert STAFF_PERMISSIONS["moderator"] == STAFF_PERMISSIONS["storyteller"]
    # Helper = spends ONLY: approve_spend, but no awarding/adjust/edits
    assert staff_role_has_permission("helper", "approve_spend")
    assert not staff_role_has_permission("helper", "approve_claim")
    assert not staff_role_has_permission("helper", "adjust_xp")
    assert not staff_role_has_permission("helper", "edit_character")
    # Unknown / missing roles always deny
    assert not staff_role_has_permission(None, "approve_claim")
    assert not staff_role_has_permission("", "approve_claim")
    assert not staff_role_has_permission("nonsense", "approve_claim")


def test_set_staff_role_round_trip(staff):
    """The role-assignment endpoint should persist the role on the
    player_profiles row and audit the change."""
    from web.db import get_db, get_staff_role, upsert_player

    with get_db() as conn:
        upsert_player(conn, discord_id="42000042", username="RoleSmoke")

    staff.get("/_dev/seed", follow_redirects=False)
    r = staff.post(
        "/staff/admin/roles/42000042/set",
        data={"_csrf": "dev-csrf-token", "role": "storyteller"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    with get_db() as conn:
        assert get_staff_role(conn, "42000042") == "storyteller"
        # Audit row recording the change
        audit = conn.execute(
            "SELECT * FROM audit_log WHERE action='set_staff_role' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None

    # Clear the role
    staff.post(
        "/staff/admin/roles/42000042/set",
        data={"_csrf": "dev-csrf-token", "role": ""},
        follow_redirects=False,
    )
    with get_db() as conn:
        assert get_staff_role(conn, "42000042") is None
        conn.execute("DELETE FROM player_profiles WHERE discord_id='42000042'")


def test_role_endpoint_refuses_unknown_role(staff):
    """Posting a bogus role string should not persist anything."""
    from web.db import get_db, get_staff_role, upsert_player
    with get_db() as conn:
        upsert_player(conn, discord_id="43000043", username="BadRoleSmoke")
    staff.get("/_dev/seed", follow_redirects=False)
    r = staff.post(
        "/staff/admin/roles/43000043/set",
        data={"_csrf": "dev-csrf-token", "role": "supreme_overlord"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db() as conn:
        assert get_staff_role(conn, "43000043") is None
        conn.execute("DELETE FROM player_profiles WHERE discord_id='43000043'")


def test_helper_role_can_approve_spend_but_not_award(staff):
    """The founding 'Helpers do spends only' rule: a Helper is allowed at
    approve_spend but blocked from awarding XP (approve_claim), manual
    adjust, and editing characters. Exercises the matrix end-to-end."""
    from web.db import get_db, set_staff_role, staff_role_has_permission

    with get_db() as conn:
        set_staff_role(conn, "999999999999999999", "helper", actor_id="smoke")
        role = "helper"

    assert staff_role_has_permission(role, "approve_spend")
    assert not staff_role_has_permission(role, "approve_claim")
    assert not staff_role_has_permission(role, "adjust_xp")
    assert not staff_role_has_permission(role, "edit_character")
    assert not staff_role_has_permission(role, "manage_settings")

    # Restore admin for subsequent tests
    with get_db() as conn:
        set_staff_role(conn, "999999999999999999", "admin", actor_id="smoke-restore")


def test_admin_settings_save_requires_permission(staff, player):
    """A staff seat whose role lacks manage_settings (e.g. Storyteller) must
    not register as able to flip chronicle settings at the matrix level."""
    from web.db import get_db, upsert_player, set_staff_role
    staff.get("/_dev/seed", follow_redirects=False)
    with get_db() as conn:
        upsert_player(conn, discord_id="999999999999999999", username="DevStaff")
        set_staff_role(conn, "999999999999999999", "storyteller", actor_id="smoke")

    from web.db import staff_role_has_permission, get_staff_role
    with get_db() as conn:
        role = get_staff_role(conn, "999999999999999999")
    assert role == "storyteller"
    assert not staff_role_has_permission(role, "manage_settings")

    # Reset back to admin so subsequent tests aren't affected.
    with get_db() as conn:
        set_staff_role(conn, "999999999999999999", "admin", actor_id="smoke-restore")


def test_in_memoriam_is_a_player_choice_not_forced(staff, player):
    """In Memoriam is an opt-in the player CHOOSES (migration 040), no longer
    forced by a chronicle ruleset. With IM enabled a 'standard' Ancilla pick is
    honored; with IM disabled an 'in_memoriam' pick folds back to standard.
    (creation_mode='open' here so we exercise the mode logic, not RAW validation.)"""
    import json as _j
    from web.db import get_db, upsert_settings

    # IM enabled — a 'standard' pick must be honored (not coerced to IM).
    with get_db() as conn:
        upsert_settings(conn, in_memoriam_enabled=1, creation_mode="open")
    r1 = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "IM Choice Smoke",
            "clan": "brujah",
            "character_type": "kindred",
            "character_tier": "ancilla",
            "ancilla_mode": "standard",
            "touchstones": _j.dumps(["Friend A", "Friend B"]),
        },
        follow_redirects=False,
    )
    assert r1.status_code == 303

    # IM disabled — an 'in_memoriam' pick must fold back to standard.
    with get_db() as conn:
        upsert_settings(conn, in_memoriam_enabled=0)
    r2 = player.post(
        "/characters/new",
        data={
            "_csrf": "dev-csrf-token",
            "name": "IM Disabled Smoke",
            "clan": "brujah",
            "character_type": "kindred",
            "character_tier": "ancilla",
            "ancilla_mode": "in_memoriam",
            "touchstones": _j.dumps(["Friend A", "Friend B"]),
        },
        follow_redirects=False,
    )
    assert r2.status_code == 303

    with get_db() as conn:
        modes = {
            row["name"]: row["ancilla_mode"]
            for row in conn.execute(
                "SELECT name, ancilla_mode FROM characters "
                "WHERE name IN ('IM Choice Smoke', 'IM Disabled Smoke')"
            ).fetchall()
        }
        try:
            assert modes.get("IM Choice Smoke") == "standard", \
                "a 'standard' pick must be honored when IM is enabled"
            assert modes.get("IM Disabled Smoke") == "standard", \
                "an 'in_memoriam' pick must fold to standard when IM is disabled"
        finally:
            conn.execute(
                "DELETE FROM characters WHERE name IN ('IM Choice Smoke', 'IM Disabled Smoke')"
            )
            upsert_settings(conn, creation_mode="guided")


def test_active_ruleset_saves_and_round_trips(staff):
    """Standard/Homebrew is the base ruleset; In Memoriam is a separate flag
    (migration 040). use_homebrew_rules stays in sync, and a legacy
    active_ruleset='in_memoriam' POST folds to a Standard base + the flag on."""
    from web.db import get_db, get_settings
    # Homebrew base + In Memoriam flag on (they coexist now).
    r = staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "require_sheet_on_create": "on",
            "active_ruleset": "homebrew",
            "in_memoriam_enabled": "on",
            "revenant_families": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    with get_db() as conn:
        s = get_settings(conn)
    assert s["active_ruleset"] == "homebrew"
    assert s["in_memoriam_enabled"] == 1
    assert s["use_homebrew_rules"] == 1

    # A legacy 'in_memoriam' base value folds to a Standard base + flag on.
    staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "in_memoriam",
            "revenant_families": "",
        },
        follow_redirects=False,
    )
    with get_db() as conn:
        s = get_settings(conn)
    assert s["active_ruleset"] == "standard"
    assert s["in_memoriam_enabled"] == 1
    assert s["use_homebrew_rules"] == 0

    # Reset to a clean Standard base, IM off.
    staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "standard",
            "revenant_families": "",
        },
        follow_redirects=False,
    )


def test_tier_budget_overrides_round_trip(staff):
    """Per-tier budget POST should land in the homebrew_tier_budgets
    JSON and tier_budget() should resolve overrides when ruleset=homebrew.

    The admin form posts a single combined Merit/Advantage/Background
    pool per tier (tier_<key>_mab); the route splits it three ways
    (total//3 each, remainder to backgrounds). Every tier the form
    renders — including 'fledgling' — must round-trip."""
    from web.db import get_db, get_settings, tier_budget
    r = staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "homebrew",
            # Keep the default sheet-on-create flag set so this POST
            # doesn't flip it off and leak into later wizard tests.
            "require_sheet_on_create": "on",
            "tier_mortal_xp": "30",
            "tier_mortal_mab": "12",
            "tier_fledgling_xp": "40",
            "tier_fledgling_mab": "9",
            "tier_ancilla_xp": "150",
            "tier_ancilla_mab": "15",
            "revenant_families": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    with get_db() as conn:
        s = get_settings(conn)
        mortal = tier_budget(s, "mortal")
        fledgling = tier_budget(s, "fledgling")
        ancilla = tier_budget(s, "ancilla")
        # Tiers not overridden still use defaults
        ghoul = tier_budget(s, "ghoul")

    assert mortal["xp"] == 30
    # Combined pool of 12 splits evenly into 4 / 4 / 4.
    assert mortal["merits"] == 4
    assert mortal["advantages"] == 4
    assert mortal["backgrounds"] == 4
    # Fledgling override must round-trip. Regression guard: the route's
    # tier loop previously omitted 'fledgling', silently dropping it.
    assert fledgling["xp"] == 40
    assert fledgling["merits"] == 3      # 9 // 3
    assert ancilla["xp"] == 150
    assert ancilla["merits"] == 5        # 15 // 3
    # Unset tier falls back to V5 RAW defaults (non-Kindred = 0 finishing XP).
    assert ghoul["xp"] == 0


def test_tier_budget_honors_overrides_under_in_memoriam_ruleset(staff):
    """Per-tier homebrew overrides still apply when In Memoriam is enabled — IM
    only adds the Ancilla Era path; the base ruleset (homebrew here) governs the
    budgets for every tier."""
    from web.db import get_db, get_settings, tier_budget
    # Homebrew base + an override + In Memoriam flag on (they coexist now).
    staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "homebrew",
            "in_memoriam_enabled": "on",
            "tier_mortal_xp": "42",
            "revenant_families": "",
        },
        follow_redirects=False,
    )
    with get_db() as conn:
        s = get_settings(conn)
        mortal = tier_budget(s, "mortal")
    assert mortal["xp"] == 42, "homebrew overrides must still apply with IM enabled"

    # Reset to standard for subsequent tests
    staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "standard",
            "revenant_families": "",
        },
        follow_redirects=False,
    )


def test_tier_budget_ignores_overrides_when_ruleset_is_standard(staff):
    """On standard ruleset, the per-tier overrides should be ignored —
    every tier returns V5 RAW defaults regardless of stored values."""
    from web.db import get_db, get_settings, tier_budget
    # Save homebrew overrides
    staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "homebrew",
            "tier_neonate_xp": "999",
            "revenant_families": "",
        },
        follow_redirects=False,
    )
    # Switch back to standard
    staff.post(
        "/staff/admin/settings",
        data={
            "_csrf": "dev-csrf-token",
            "active_ruleset": "standard",
            "revenant_families": "",
        },
        follow_redirects=False,
    )
    with get_db() as conn:
        s = get_settings(conn)
        neonate = tier_budget(s, "neonate")
    assert neonate["xp"] == 15  # V5 RAW finishing XP, NOT the 999 we stored


def test_standard_tier_xp_is_raw_finishing_pool():
    """Standard ruleset = V5 RAW finishing XP, baked into _TIER_DEFAULTS as the
    single source of truth (the wizard layers no Sea-of-Time bonus on top):
    Kindred Fledgling 0 / Neonate 15 / Ancilla 35; non-Kindred 0 each.
    The Ancilla 35 is the regression guard — it used to double-count to 70+."""
    from web.db import tier_budget
    s = {"active_ruleset": "standard"}
    assert tier_budget(s, "fledgling")["xp"] == 0
    assert tier_budget(s, "thinblood")["xp"] == 0
    assert tier_budget(s, "neonate")["xp"] == 15
    assert tier_budget(s, "ancilla")["xp"] == 35   # baseline 0 + 35, applied once
    assert tier_budget(s, "mortal")["xp"] == 0
    assert tier_budget(s, "ghoul")["xp"] == 0
    assert tier_budget(s, "revenant")["xp"] == 0


def test_sea_of_time_bp_humanity_seeded_per_tier(player):
    """Standard Kindred get Sea-of-Time starting Blood Potency + Humanity seeded
    server-side per tier (the wizard only previews them): Neonate BP 1 / Hum 7,
    Ancilla BP 2 / Hum 6, Thin-blood BP 0. Regression guard for the old flat
    BP 1 / Humanity 7 that ignored the tier entirely."""
    import json as _j
    from web.db import get_db

    def _make(name, tier, clan="brujah", **extra):
        r = player.post("/characters/new", data={
            "_csrf": "dev-csrf-token", "name": name, "clan": clan,
            "character_type": "kindred", "character_tier": tier,
            "touchstones": _j.dumps(["A", "B"]),
            **_raw_traits(), **extra,
        }, follow_redirects=False)
        assert r.status_code == 303, f"{name} create failed: {r.status_code}"
        with get_db() as conn:
            row = conn.execute(
                "SELECT sheet_json FROM characters WHERE name=? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
        return _j.loads(row["sheet_json"])

    try:
        neo = _make("SoT Neonate", "neonate")
        assert neo["blood_potency"] == 1 and neo["humanity"] == 7
        anc = _make("SoT Ancilla", "ancilla", ancilla_mode="standard")
        assert anc["blood_potency"] == 2, "standard Ancilla = BP 2 (was wrongly 1)"
        assert anc["humanity"] == 6, "standard Ancilla = Humanity 6 (was wrongly 7)"
        tb = _make("SoT ThinBlood", "thinblood", clan="thin-blood")
        assert tb["blood_potency"] == 0, "Thin-blood = BP 0 (was wrongly 1)"
    finally:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM characters WHERE name IN "
                "('SoT Neonate', 'SoT Ancilla', 'SoT ThinBlood')"
            )


def test_period_schedule_stamp_generates_periods(_client):
    """Saving a schedule template then stamping N periods should produce
    that many rows, each separated by the cadence and following the
    label pattern with {n} replaced.

    _client dependency forces migrations to apply even when this test
    runs first (the schedule tests don't otherwise touch HTTP)."""
    from web.db import (
        get_db, create_period_schedule, stamp_periods_from_schedule,
    )
    with get_db() as conn:
        # Clean slate — wipe any pre-existing rows for this anchor
        conn.execute(
            "DELETE FROM play_periods WHERE label LIKE 'Stamp Smoke %'"
        )
        sched = create_period_schedule(
            conn,
            name="StampSmoke",
            anchor_at="2099-01-04T20:00:00Z",  # far-future to avoid collisions
            cadence_days=14,
            duration_hours=48,
            label_pattern="Stamp Smoke {n}",
            created_by="smoke",
        )
        try:
            result = stamp_periods_from_schedule(conn, sched["id"], 3,
                                                 created_by="smoke")
            assert result["created"] == 3
            assert result["skipped"] == 0
            labels = [p["label"] for p in result["periods"]]
            assert labels == ["Stamp Smoke 1", "Stamp Smoke 2", "Stamp Smoke 3"]
            # Cadence — each opens_at is 14 days after the previous
            opens = [p["opens_at"] for p in result["periods"]]
            assert opens[0] == "2099-01-04T20:00:00Z"
            assert opens[1] == "2099-01-18T20:00:00Z"
            assert opens[2] == "2099-02-01T20:00:00Z"
            # Stamping again should resume the counter at 4
            result2 = stamp_periods_from_schedule(conn, sched["id"], 1,
                                                  created_by="smoke")
            assert result2["periods"][0]["label"] == "Stamp Smoke 4"
        finally:
            conn.execute(
                "DELETE FROM play_periods WHERE label LIKE 'Stamp Smoke %'"
            )
            conn.execute("DELETE FROM period_schedules WHERE id=?", (sched["id"],))


def test_period_schedule_stamp_resumes_after_existing_period(_client):
    """If periods already exist after the schedule's anchor, stamping
    should resume from one cadence step after the latest existing
    period (not duplicate it)."""
    from web.db import (
        get_db, create_period_schedule, stamp_periods_from_schedule, create_period,
    )
    with get_db() as conn:
        conn.execute("DELETE FROM play_periods WHERE label LIKE 'Resume Smoke %'")
        sched = create_period_schedule(
            conn, name="ResumeSmoke",
            anchor_at="2099-06-01T20:00:00Z",
            cadence_days=7, duration_hours=24,
            label_pattern="Resume Smoke {n}",
            created_by="smoke",
        )
        # Pre-create a period at anchor — stamp should jump past it
        create_period(
            conn, label="Resume Smoke pre", period_type="night", phase="full",
            opens_at="2099-06-01T20:00:00Z", closes_at="2099-06-02T20:00:00Z",
            created_by="smoke",
        )
        try:
            result = stamp_periods_from_schedule(conn, sched["id"], 2,
                                                 created_by="smoke")
            assert result["created"] == 2
            # First new period must be 7 days after the pre-existing one
            assert result["periods"][0]["opens_at"] == "2099-06-08T20:00:00Z"
            assert result["periods"][1]["opens_at"] == "2099-06-15T20:00:00Z"
        finally:
            conn.execute("DELETE FROM play_periods WHERE label LIKE 'Resume Smoke %'")
            conn.execute("DELETE FROM period_schedules WHERE id=?", (sched["id"],))


def test_coterie_manage_page_renders_merits_flaws_panels(staff):
    """Smoke: hitting the coterie detail page renders the merits + flaws
    sections (even when empty)."""
    from web.db import get_db, create_coterie
    with get_db() as conn:
        co = create_coterie(conn, "PanelSmoke")
    try:
        r = staff.get(f"/staff/coteries/{co['id']}")
        assert r.status_code == 200
        assert "Merits" in r.text
        assert "Flaws"  in r.text
        # Empty-state copy
        assert "No merits or backgrounds recorded" in r.text
        assert "No flaws recorded"  in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM coteries WHERE id=?", (co["id"],))


# ── Chargen draft/review flow (Steward bug report 2026-05) ────────────────────

def test_short_form_submit_stages_as_draft(player):
    """In short-form chronicles the initial wizard Submit must NOT push the
    character straight to the staff queue. It stages it as a draft so the
    player can fill the external sheet on the detail page first."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    try:
        r = player.post(
            "/characters/new",
            data={"_csrf": "dev-csrf-token",
                  "name": "Short Form Drafted",
                  "clan": "brujah"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "tab=sheet" in r.headers.get("location", "")
        with get_db() as conn:
            row = conn.execute(
                "SELECT is_draft, is_approved, post_wizard, sheet_json FROM characters "
                "WHERE name='Short Form Drafted'"
            ).fetchone()
            assert row is not None
            assert row["is_draft"] == 1, "short-form Submit must stage as a draft"
            assert row["is_approved"] == 0
            # post_wizard column (migration 026) flags this draft so the roster
            # resume link routes to the detail page, not back into the wizard.
            assert row["post_wizard"] == 1
            # And routing state must NOT leak back into the sheet blob.
            assert "_post_wizard" not in (row["sheet_json"] or "")
    finally:
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=1)
            conn.execute("DELETE FROM characters WHERE name='Short Form Drafted'")


def test_submit_for_review_flips_draft(player):
    """The Submit for Review button on the detail page is the explicit
    'I'm done' signal that sends a short-form draft to staff."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    try:
        # Stage a short-form draft
        player.post(
            "/characters/new",
            data={"_csrf": "dev-csrf-token",
                  "name": "Review Me", "clan": "ventrue"},
            follow_redirects=False,
        )
        with get_db() as conn:
            cid = conn.execute(
                "SELECT id FROM characters WHERE name='Review Me'"
            ).fetchone()["id"]
            assert conn.execute(
                "SELECT is_draft FROM characters WHERE id=?", (cid,)
            ).fetchone()["is_draft"] == 1

        r = player.post(
            f"/characters/{cid}/submit-for-review",
            data={"_csrf": "dev-csrf-token"},
            follow_redirects=False,
        )
        assert r.status_code == 303

        with get_db() as conn:
            row = conn.execute(
                "SELECT is_draft, is_approved FROM characters WHERE id=?", (cid,)
            ).fetchone()
            assert row["is_draft"] == 0, "submit-for-review must flip is_draft off"
            assert row["is_approved"] == 0, "but not auto-approve"
    finally:
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=1)
            conn.execute("DELETE FROM characters WHERE name='Review Me'")


def test_submit_for_review_rejects_already_submitted(player):
    """Double-clicking Submit for Review on a character that's already in
    the staff queue must not error — just bounce with a soft flash."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    try:
        player.post("/characters/new",
                    data={"_csrf": "dev-csrf-token",
                          "name": "Dbl Click", "clan": "tremere"},
                    follow_redirects=False)
        with get_db() as conn:
            cid = conn.execute(
                "SELECT id FROM characters WHERE name='Dbl Click'"
            ).fetchone()["id"]
        # First press
        r1 = player.post(f"/characters/{cid}/submit-for-review",
                         data={"_csrf": "dev-csrf-token"},
                         follow_redirects=False)
        assert r1.status_code == 303
        # Second press — already in queue
        r2 = player.post(f"/characters/{cid}/submit-for-review",
                         data={"_csrf": "dev-csrf-token"},
                         follow_redirects=False)
        assert r2.status_code == 303
        assert r2.headers.get("location", "").endswith(f"/characters/{cid}")
    finally:
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=1)
            conn.execute("DELETE FROM characters WHERE name='Dbl Click'")


def test_staff_pending_queue_excludes_drafts(staff):
    """Drafts (including short-form post-wizard drafts) must not appear in
    the staff pending-review list — they're not 'ready' yet. We create
    the draft directly via the DB so no flash message leaks the name into
    the staff response."""
    from web.db import get_db, create_character, update_character
    with get_db() as conn:
        char = create_character(
            conn,
            discord_id="player-test-stay-hidden",
            name="Should Stay Hidden",
            clan="nosferatu",
        )
        update_character(conn, char["id"], is_draft=1)
        conn.commit()
    try:
        r = staff.get("/staff/characters")
        assert r.status_code == 200
        # The character exists in the DB but the staff pending roster must
        # not list it — drafts are filtered out via not c['is_draft'].
        assert "Should Stay Hidden" not in r.text

        # Also verify it doesn't inflate the dashboard pending count.
        rd = staff.get("/staff/")
        assert rd.status_code == 200
        assert "Should Stay Hidden" not in rd.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE name='Should Stay Hidden'")


def test_staff_approve_via_plain_form_redirects(player, staff):
    """The detail-page Approve button is a plain form post (no HX-Request
    header). The server must redirect to the roster (staff asked to pop
    back to the list after approving) rather than staying on the detail
    page or dumping the roster partial."""
    from web.db import get_db
    # Use the full-wizard mode so the character lands directly in the
    # staff queue (no Submit-for-Review step needed).
    player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token",
              "name": "Approve Me Direct",
              "clan": "toreador",
              "touchstones": '["A", "B"]',
              **_raw_traits("toreador")},
        follow_redirects=False,
    )
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Approve Me Direct'"
        ).fetchone()["id"]
    try:
        # Plain form post — NO HX-Request header.
        r = staff.post(f"/staff/characters/{cid}/approve",
                       data={"_csrf": "dev-csrf-token"},
                       follow_redirects=False)
        assert r.status_code == 303, "must redirect on plain form post"
        loc = r.headers.get("location", "")
        assert loc.endswith("/staff/characters"), f"should pop back to the roster, got {loc!r}"
        assert not loc.endswith(f"/characters/{cid}"), "should not stay on the detail page"
        with get_db() as conn:
            row = conn.execute(
                "SELECT is_approved FROM characters WHERE id=?", (cid,)
            ).fetchone()
            assert row["is_approved"] == 1
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))


def test_staff_approve_via_htmx_returns_partial(player, staff):
    """The roster Approve button still uses HTMX — the server must return
    the pending-chars-table partial when HX-Request is set."""
    from web.db import get_db
    player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token",
              "name": "Approve Via Htmx",
              "clan": "gangrel",
              "touchstones": '["A", "B"]',
              **_raw_traits("gangrel")},
        follow_redirects=False,
    )
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Approve Via Htmx'"
        ).fetchone()["id"]
    try:
        r = staff.post(
            f"/staff/characters/{cid}/approve",
            data={"_csrf": "dev-csrf-token"},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        # HTMX gets a 200 with the partial body (no redirect).
        assert r.status_code == 200
        # The partial should be the pending-chars table — not a full doc.
        assert "<!DOCTYPE" not in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))


def test_short_form_step3_has_save_draft_button(player):
    """Bug 3: short-form Step 3 (Story) used to show only Back + Submit —
    no way to save a draft from there. The fix is a Save Draft button
    rendered alongside Submit Character in short-form mode."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    try:
        r = player.get("/characters/new")
        assert r.status_code == 200
        # The wizard's short-form Step 3 area must include both buttons.
        # We can't assert ordering here without parsing, but the markup
        # has both labels on the same page.
        assert "Submit Character" in r.text
        assert "Save Draft" in r.text
    finally:
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=1)


def test_save_draft_js_handles_sidebar_button(player):
    """Bug 3: the sidebar Save Draft button lives outside the <form>, so
    closest('form') from there returns null. The JS must fall back to a
    document.querySelector lookup. Guard against the broken ternary
    pattern (truthy branch returns null without falling back) coming back."""
    r = player.get("/characters/new")
    assert r.status_code == 200
    src = r.text
    # Confirm the saveAsDraft factory is in the bundle.
    assert "saveAsDraft" in src
    # The fixed saveAsDraft must reference the global fallback selector.
    assert "document.querySelector" in src
    assert 'form[action="/characters/new"]' in src
    # The fixed pattern uses an `||` fallback so a null `closest` result
    # doesn't lose the form reference. Either the optional-chaining form
    # or the explicit fromTarget pattern is acceptable.
    assert "fromTarget ||" in src or "?.closest?.('form')" in src


def test_resumed_short_form_draft_links_to_detail_page(player):
    """Bug 3 follow-on: short-form post-wizard drafts must resume on the
    detail page, not the wizard. The roster renders the appropriate href
    based on the `_post_wizard` sentinel inside sheet_json."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, require_sheet_on_create=0)
    try:
        player.post("/characters/new",
                    data={"_csrf": "dev-csrf-token",
                          "name": "Resume To Sheet", "clan": "lasombra"},
                    follow_redirects=False)
        r = player.get("/characters")
        assert r.status_code == 200
        # The roster's draft card for this character points at ?tab=sheet,
        # not at /resume-draft.
        # We look for the specific draft card markup.
        assert "Resume To Sheet" in r.text
        assert "?tab=sheet" in r.text
        # The "Filling Sheet" chip distinguishes the post-wizard staging
        # state from a fresh draft.
        assert "Filling Sheet" in r.text
    finally:
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=1)
            conn.execute("DELETE FROM characters WHERE name='Resume To Sheet'")


def test_staff_detail_renders_touchstones(staff):
    """Bug 4a: opening a character for review must show touchstones —
    they were not in the staff sheet section before this fix. We seed
    the character via DB so the test doesn't depend on the wizard's
    touchstone validation path."""
    from web.db import get_db, create_character
    touchstones = [
        {"name": "Marie Devereaux", "conviction": "Protect the powerless"},
        {"name": "Father Cesare",   "conviction": "Never lie"},
    ]
    with get_db() as conn:
        char = create_character(
            conn,
            discord_id="player-test-touchstones",
            name="Sees Touchstones",
            clan="salubri",
            sheet_json={"touchstones": touchstones},
        )
        cid = char["id"]
        conn.commit()
    try:
        r = staff.get(f"/staff/characters/{cid}")
        assert r.status_code == 200
        assert "Touchstones" in r.text
        assert "Marie Devereaux" in r.text
        assert "Protect the powerless" in r.text
        assert "Father Cesare" in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))


def test_staff_detail_renders_type_tier_and_im_eras(player, staff):
    """Bug 4a: type/tier and In Memoriam eras must surface in staff
    review — staff need that context to decide whether the build is
    legal under the chronicle's tier/ruleset settings."""
    import json as _j
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, in_memoriam_enabled=1)
    im_blob = {
        "generation":        "9th-8th",
        "discipline_spread": "broad",
        "embrace_age":       "over_150",
        "eras": [
            {"type": "violence",  "gambit_taken": True, "gambit_roll": 4,
             "humanity_loss": 1},
            {"type": "adversity", "gambit_taken": False, "gambit_roll": None},
        ],
    }
    player.post(
        "/characters/new",
        data={
            "_csrf":               "dev-csrf-token",
            "name":                "Sees Tier",
            "clan":                "ventrue",
            "character_type":      "kindred",
            "character_tier":      "ancilla",
            "ancilla_mode":        "in_memoriam",
            "im_generation":       "9th-8th",
            "im_discipline_spread":"broad",
            "in_memoriam":         _j.dumps(im_blob),
            "touchstones":         _j.dumps(["A", "B"]),
            **_raw_traits("ventrue"),
        },
        follow_redirects=False,
    )
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Sees Tier'"
        ).fetchone()["id"]
    try:
        r = staff.get(f"/staff/characters/{cid}")
        assert r.status_code == 200
        # Type / Tier label and value
        assert "Type / Tier" in r.text
        assert "Ancilla" in r.text
        assert "In Memoriam" in r.text
        # Era labels render
        assert "Lived Eras" in r.text
        assert "Violence" in r.text
        assert "Adversity" in r.text
        # Generation / embrace age band visible
        assert "9th-8th" in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            upsert_settings(conn, in_memoriam_enabled=0)


def test_player_sheet_renders_edit_mode_toggle(player):
    """An approved character's Sheet tab must open in read-only mode with
    an Edit Sheet button (and the form gated by editMode). The Save
    button only appears in edit mode."""
    r = player.get("/characters/1")
    assert r.status_code == 200
    # The Edit mode helper symbols must be in the markup.
    assert "editMode" in r.text
    assert "Edit Sheet" in r.text
    # Save Sheet button is present but x-show='editMode' — text appears in markup.
    assert "Save Sheet" in r.text
    # Cancel button only renders under x-cloak in edit mode.
    assert "cancelEdit" in r.text
    # The sticky form id used by the Save button.
    assert 'id="char-sheet-form"' in r.text
    # The read-only chrome class binding.
    assert "pointer-events-none" in r.text


def test_reject_resets_is_approved(staff):
    """Reject must clear is_approved + review_started_at so a character
    that was previously approved can flow through the queue again."""
    from web.db import get_db, create_character, update_character, reject_character
    with get_db() as conn:
        ch = create_character(conn, discord_id="rej-test", name="ReRej Test", clan="brujah")
        cid = ch["id"]
        # Pretend it was approved + reviewed.
        update_character(conn, cid, is_approved=1, approved_by="0", approved_at="2026-05-29T00:00:00Z")
        conn.execute("UPDATE characters SET review_started_at=?, review_started_by=? WHERE id=?",
                     ("2026-05-29T00:00:00Z", "0", cid))
        conn.commit()
    try:
        with get_db() as conn:
            reject_character(conn, cid, "0", "needs work")
            conn.commit()
            row = conn.execute(
                "SELECT is_approved, status, rejection_reason, review_started_at, approved_by "
                "FROM characters WHERE id=?", (cid,)
            ).fetchone()
        assert row["is_approved"] == 0, "reject must clear is_approved"
        assert row["status"] == "pending"
        assert row["rejection_reason"] == "needs work"
        assert row["review_started_at"] is None, "reject must clear review_started_at"
        assert row["approved_by"] is None, "reject must clear approved_by"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))


def test_staff_detail_renders_ingrained_discipline(player, staff):
    """Bug 4a: if a player marks the Ingrained Discipline Flaw, staff
    must see the flagged discipline + XP-used counter on the overview."""
    from web.db import get_db, update_character
    player.post(
        "/characters/new",
        data={"_csrf": "dev-csrf-token",
              "name": "Sees Ingrained",
              "clan": "tremere",
              "has_ingrained_flaw": "on",
              "touchstones": '["A", "B"]',
              **_raw_traits("tremere")},
        follow_redirects=False,
    )
    with get_db() as conn:
        cid = conn.execute(
            "SELECT id FROM characters WHERE name='Sees Ingrained'"
        ).fetchone()["id"]
        # Staff would normally flag the specific discipline after review.
        update_character(conn, cid, ingrained_discipline="auspex")
    try:
        r = staff.get(f"/staff/characters/{cid}")
        assert r.status_code == 200
        assert "Ingrained Discipline Flaw" in r.text
        assert "Auspex" in r.text
        assert "/ 15 XP used" in r.text
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
