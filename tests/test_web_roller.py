"""Web V5 dice roller — the shared mechanical-state conditions helper plus the
Roll tab and /roll + /roll/reroll routes.

The roll itself is random, so the route tests assert structure (status, the
result panel, a valid outcome label) rather than specific dice.
"""
import random

import pytest

from core.conditions import SEV_CRIT, SEV_WARN, character_conditions
from core.resonance import RESONANCES, TEMPERAMENTS, roll_resonance


@pytest.fixture(autouse=True)
def _roller_enabled(_client):
    """Keep the dice-roller toggle ON before each test — other suites' partial
    admin-settings POSTs can switch it off in the shared test DB."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", dice_roller_enabled=1)
        conn.commit()
    yield


# ── conditions helper (pure) ──────────────────────────────────────────────────

def test_conditions_empty_for_healthy_sheet():
    sheet = {"attr_stamina": 3, "attr_composure": 3, "attr_resolve": 3,
             "hunger": 1, "humanity": 7}
    assert character_conditions(sheet) == []


def test_conditions_flags_full_health_impaired():
    # Stamina 2 → 5 Health boxes; all marked Superficial = Impaired.
    sheet = {"attr_stamina": 2, "damage_health_sup": 5, "hunger": 0, "humanity": 7}
    by_key = {c["key"]: c for c in character_conditions(sheet)}
    assert "health" in by_key and by_key["health"]["sev"] == SEV_CRIT


def test_conditions_flags_ravenous_hunger_five():
    by_key = {c["key"]: c for c in character_conditions({"hunger": 5, "humanity": 7})}
    assert "hunger" in by_key and by_key["hunger"]["sev"] == SEV_CRIT


def test_conditions_flags_low_humanity_warn():
    by_key = {c["key"]: c for c in character_conditions({"hunger": 1, "humanity": 2})}
    assert by_key["humanity"]["sev"] == SEV_WARN


def test_conditions_robust_to_junk():
    assert character_conditions(None) == []
    assert character_conditions({}) == []   # no traits → no full-track flags


# ── roll routes (integration) ─────────────────────────────────────────────────

_OUTCOMES = ("Critical Win", "Messy Critical", "Success", "Failure",
             "Total Failure", "Bestial Failure")


def test_roll_tab_present_on_character_page(player):
    r = player.get("/characters/1")
    assert r.status_code == 200
    assert "tab === 'roll'" in r.text and 'id="roll-panel"' in r.text


def test_roll_numeric_pool(player):
    r = player.post("/characters/1/roll",
                    data={"_csrf": "dev-csrf-token", "pool": "5", "difficulty": "0"})
    assert r.status_code == 200
    assert 'id="roll-panel"' in r.text
    assert "5d" in r.text                                  # clean pool label
    assert any(lbl in r.text for lbl in _OUTCOMES)


def test_roll_trait_pool_resolves_from_sheet(player):
    r = player.post("/characters/1/roll",
                    data={"_csrf": "dev-csrf-token",
                          "pool": "strength + brawl", "difficulty": "0"})
    assert r.status_code == 200
    assert 'id="roll-panel"' in r.text
    assert any(lbl in r.text for lbl in _OUTCOMES)


def test_roll_empty_pool_errors(player):
    r = player.post("/characters/1/roll",
                    data={"_csrf": "dev-csrf-token", "pool": "", "difficulty": "0"})
    assert r.status_code == 200
    assert "Enter a pool" in r.text


def test_reroll_route_flags_willpower_cost(player):
    # All three normal dice are failures (<6); the reroll re-rolls them.
    r = player.post("/characters/1/roll/reroll",
                    data={"_csrf": "dev-csrf-token", "normal": "3,4,5",
                          "hunger": "7", "difficulty": "0", "pool_label": "5d",
                          "pool": "5"})
    assert r.status_code == 200
    assert "Willpower reroll" in r.text
    assert "Superficial Willpower" in r.text


def test_roll_tab_respects_chronicle_toggle(player):
    """The Roll tab is chronicle-toggleable (dice_roller_enabled, migration 051):
    off removes it, on restores it."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", dice_roller_enabled=0)
        conn.commit()
    try:
        assert "tab === 'roll'" not in player.get("/characters/1").text
    finally:
        with get_db() as conn:
            upsert_settings(conn, actor_id="t", dice_roller_enabled=1)
            conn.commit()
    assert "tab === 'roll'" in player.get("/characters/1").text


# ── Resonance & Temperament generator ─────────────────────────────────────────

def test_roll_resonance_structure():
    for seed in range(25):
        rr = roll_resonance(random.Random(seed))
        assert rr["resonance"] in RESONANCES
        assert rr["temperament"] in {t[0] for t in TEMPERAMENTS}
        assert len(rr["disciplines"]) == 2
        assert rr["has_bonus"] == (rr["temperament"] in ("intense", "acute"))
        assert rr["is_acute"] == (rr["temperament"] == "acute")


def test_roll_resonance_seeded_deterministic():
    assert roll_resonance(random.Random(42)) == roll_resonance(random.Random(42))


def test_temperament_weights_sum_to_100():
    assert sum(t[2] for t in TEMPERAMENTS) == 100


def test_resonance_route(player):
    r = player.post("/characters/1/resonance", data={"_csrf": "dev-csrf-token"})
    assert r.status_code == 200
    assert 'id="resonance-panel"' in r.text
    assert any(RESONANCES[k]["label"] in r.text for k in RESONANCES)
