"""Web V5 dice roller — the shared mechanical-state conditions helper plus the
Roll tab and /roll + /roll/reroll routes.

The roll itself is random, so the route tests assert structure (status, the
result panel, a valid outcome label) rather than specific dice.
"""
import random

import pytest

from core.conditions import SEV_CRIT, SEV_WARN, character_conditions
from core.dyscrasias import DYSCRASIAS
from core.resonance import (
    RESONANCES,
    STANDARD_DISCIPLINES,
    TATTERED_DISCIPLINES,
    TEMPERAMENTS,
    get_dyscrasia,
    roll_resonance,
)


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
    for seed in range(40):
        rr = roll_resonance(rng=random.Random(seed))
        assert rr["temperament"] in {t[0] for t in TEMPERAMENTS}
        if rr["temperament"] == "negligible":
            assert rr["resonance"] is None and rr["disciplines"] == []
        else:
            assert rr["resonance"] in RESONANCES
            assert len(rr["disciplines"]) >= 2
        assert rr["has_bonus"] == (rr["temperament"] in ("intense", "acute"))
        assert rr["is_acute"] == (rr["temperament"] == "acute")


def test_roll_resonance_seeded_deterministic():
    assert roll_resonance(rng=random.Random(42)) == roll_resonance(rng=random.Random(42))


def test_temperament_weights_sum_to_100():
    assert sum(t[2] for t in TEMPERAMENTS) == 100


def test_tattered_facade_discipline_maps():
    assert STANDARD_DISCIPLINES["choleric"] == ["Celerity", "Potence"]
    assert "Animalism" in TATTERED_DISCIPLINES["choleric"]
    assert "Oblivion" in TATTERED_DISCIPLINES["melancholic"]
    assert "Protean" in TATTERED_DISCIPLINES["sanguine"]
    assert TATTERED_DISCIPLINES["phlegmatic"] == STANDARD_DISCIPLINES["phlegmatic"]


def test_tattered_mode_roll_uses_tattered_disciplines():
    for seed in range(300):
        rr = roll_resonance("tattered_facade", random.Random(seed))
        if rr["resonance"] == "choleric":
            assert "Animalism" in rr["disciplines"]
            return
    raise AssertionError("no choleric result across 300 seeds")


def test_standard_never_empty_but_add_empty_can():
    std_seen, emp_seen = set(), set()
    for seed in range(500):
        std_seen.add(roll_resonance("standard", random.Random(seed))["resonance"])
        emp_seen.add(roll_resonance("add_empty", random.Random(seed))["resonance"])
    assert "empty" not in std_seen
    assert "empty" in emp_seen


def test_acute_result_carries_a_dyscrasia():
    for seed in range(600):
        rr = roll_resonance("standard", random.Random(seed))
        if rr["is_acute"] and rr["resonance"]:
            assert rr["dyscrasia"] and rr["dyscrasia"]["name"] and rr["dyscrasia"]["description"]
            return
    raise AssertionError("no acute result across 600 seeds")


def test_get_dyscrasia_and_coverage():
    assert set(DYSCRASIAS) == {"choleric", "melancholic", "phlegmatic", "sanguine"}
    assert sum(len(v) for v in DYSCRASIAS.values()) == 26
    d = get_dyscrasia("choleric", random.Random(1))
    assert d["name"] and isinstance(d["page"], int)
    assert get_dyscrasia("empty") is None


def test_resonance_mode_setting_roundtrip(player):
    from web.db import get_db, get_resonance_mode, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="t", resonance_mode="tattered_facade")
        conn.commit()
        assert get_resonance_mode(conn) == "tattered_facade"
        upsert_settings(conn, actor_id="t", resonance_mode="standard")
        conn.commit()


def test_resonance_route(player):
    r = player.post("/characters/1/resonance", data={"_csrf": "dev-csrf-token"})
    assert r.status_code == 200
    assert 'id="resonance-panel"' in r.text
    assert any(t[1] in r.text for t in TEMPERAMENTS)  # a temperament label always shows


# ── Phase 4 polish: selective reroll, odds preview, Hunger write-back ──────────

def test_reroll_indices_chosen_capped_and_fallback():
    from core.dice import reroll_indices
    normal = [2, 3, 4, 9, 9]            # 0,1,2 fail; 3,4 succeed
    _, n = reroll_indices(normal, [7], indices=[0, 1], rng=random.Random(5))
    assert n == 2
    _, n2 = reroll_indices(normal, [7], indices=[0, 1, 2, 3, 4], rng=random.Random(5))
    assert n2 == 3                      # capped at 3
    _, n3 = reroll_indices(normal, [7], indices=None, rng=random.Random(5))
    assert n3 == 3                      # empty → reroll the failures


def test_probability_bounds():
    from core.dice import probability
    assert probability(0, 0, 1, trials=200, rng=random.Random(1))["p_success"] == 0.0
    big = probability(10, 0, 1, trials=1000, rng=random.Random(1))
    assert big["p_success"] > 0.95
    assert big["mean_successes"] > 3
    assert 0.0 <= big["p_messy"] <= 1.0


def test_odds_route(player):
    r = player.post("/characters/1/roll/odds",
                    data={"_csrf": "dev-csrf-token", "pool": "6", "difficulty": "3"})
    assert r.status_code == 200
    assert "Odds" in r.text and "%" in r.text


def test_apply_character_state_delta_clamps(player):
    from web.db import apply_character_state_delta, get_character, get_db
    with get_db() as conn:
        apply_character_state_delta(conn, 1, hunger=-99)          # zero it
        assert apply_character_state_delta(conn, 1, hunger=3)["hunger"] == 3
        assert apply_character_state_delta(conn, 1, hunger=99)["hunger"] == 5   # clamp
        apply_character_state_delta(conn, 1, hunger=-99)          # revert
        conn.commit()
        assert (get_character(conn, 1)["sheet_json"].get("hunger") or 0) == 0
