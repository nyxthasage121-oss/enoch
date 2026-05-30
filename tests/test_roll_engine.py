"""Unit tests for the V5 dice roll engine (bot/roll.py).

These exercise the V5 scoring rules with hand-picked dice (via ``classify``)
so the logic is deterministic, plus a couple of seeded ``roll_pool`` checks.
"""
import os
import random

# bot/config import side-effects guard (roll.py doesn't import it, but keep
# parity with the other bot test in case import order pulls config later).
os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS", "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from bot.roll import (  # noqa: E402
    classify, roll_pool, build_trait_index, resolve_pool,
    reroll_failures, rouse_check,
    CRITICAL, MESSY_CRITICAL, SUCCESS, FAILURE,
    TOTAL_FAILURE, BESTIAL_FAILURE,
)


def test_success_counting_six_through_ten():
    # 7, 8, 6 are successes; 5 and 2 are not.
    r = classify([7, 8, 6, 5, 2], [], difficulty=0)
    assert r.successes == 3
    assert r.outcome == SUCCESS


def test_total_failure_zero_successes():
    r = classify([5, 4, 3, 2], [], difficulty=0)
    assert r.successes == 0
    assert r.outcome == TOTAL_FAILURE


def test_critical_pair_of_tens_adds_two():
    # Two 10s = 4 successes (2 base + 2 bonus); a winning critical.
    r = classify([10, 10], [], difficulty=2)
    assert r.successes == 4
    assert r.critical is True
    assert r.outcome == CRITICAL


def test_three_tens_one_pair_plus_single():
    # 3 tens => one pair (4) + one single (1) = 5 successes.
    r = classify([10, 10, 10], [], difficulty=0)
    assert r.successes == 5
    assert r.critical is True


def test_messy_critical_when_hunger_ten_in_pair():
    # One normal 10 + one Hunger 10 => critical pair with a Hunger 10 = messy.
    r = classify([10], [10], difficulty=2)
    assert r.successes == 4
    assert r.outcome == MESSY_CRITICAL
    assert r.messy is True


def test_critical_that_misses_difficulty_is_failure_not_crit():
    # A pair of 10s (4 successes) but difficulty 6 => failed, so NOT a crit.
    r = classify([10, 10], [], difficulty=6)
    assert r.successes == 4
    assert r.outcome == FAILURE
    assert r.is_win is False


def test_bestial_failure_on_hunger_one_when_failing():
    # No successes and a Hunger die shows 1 => bestial failure.
    r = classify([4, 3], [1, 5], difficulty=2)
    assert r.successes == 0
    assert r.outcome == BESTIAL_FAILURE
    assert r.bestial is True


def test_hunger_one_on_a_win_is_not_bestial():
    # Hunger 1 only matters on a failed roll; here we succeed.
    r = classify([8, 7], [1], difficulty=1)
    assert r.outcome == SUCCESS
    assert r.bestial is False


def test_margin_reports_distance_from_difficulty():
    r = classify([8, 8, 8], [], difficulty=2)
    assert r.successes == 3
    assert r.margin == 1


def test_no_difficulty_means_any_success_wins():
    r = classify([6], [], difficulty=0)
    assert r.outcome == SUCCESS
    r2 = classify([5], [], difficulty=0)
    assert r2.outcome == TOTAL_FAILURE


def test_roll_pool_caps_hunger_at_pool_and_five():
    res = roll_pool(pool=3, hunger=9, rng=random.Random(1))
    assert res.pool == 3
    assert res.hunger == 3            # capped to the pool size
    assert len(res.hunger_dice) == 3
    assert len(res.normal_dice) == 0

    res2 = roll_pool(pool=10, hunger=9, rng=random.Random(1))
    assert res2.hunger == 5           # capped to the V5 max of 5
    assert len(res2.normal_dice) == 5


def test_roll_pool_is_deterministic_with_seed():
    a = roll_pool(pool=6, hunger=2, difficulty=3, rng=random.Random(42))
    b = roll_pool(pool=6, hunger=2, difficulty=3, rng=random.Random(42))
    assert a.normal_dice == b.normal_dice
    assert a.hunger_dice == b.hunger_dice
    assert a.successes == b.successes
    assert a.outcome == b.outcome


def test_dice_are_sorted_descending_for_display():
    r = classify([3, 9, 1, 7], [10, 2], difficulty=0)
    assert r.normal_dice == [9, 7, 3, 1]
    assert r.hunger_dice == [10, 2]


# ── Trait-pool resolution ────────────────────────────────────────────────────

def test_build_trait_index_maps_labels_and_key_suffixes():
    idx = build_trait_index(
        [("attr_strength", "Strength")],
        [("skill_brawl", "Brawl")],
        [("disc_blood_sorcery", "Blood Sorcery")],
    )
    assert idx["strength"] == "attr_strength"
    assert idx["brawl"] == "skill_brawl"
    # Multi-word label and the bare key suffix both resolve.
    assert idx["blood sorcery"] == "disc_blood_sorcery"


def test_resolve_pool_sums_traits_and_flat_modifier():
    idx = build_trait_index(
        [("attr_strength", "Strength")],
        [("skill_brawl", "Brawl")],
    )
    sheet = {"attr_strength": 3, "skill_brawl": 2}
    pool, parts, unknown = resolve_pool("strength + brawl + 1", sheet, idx)
    assert pool == 6
    assert unknown == []
    assert {p[0] for p in parts} >= {"Strength", "Brawl"}


def test_resolve_pool_flags_unknown_tokens():
    idx = build_trait_index([("attr_strength", "Strength")])
    pool, _parts, unknown = resolve_pool("strength + bogus", {"attr_strength": 4}, idx)
    assert pool == 4
    assert unknown == ["bogus"]


def test_resolve_pool_handles_multiword_and_missing_dots():
    idx = build_trait_index([("disc_blood_sorcery", "Blood Sorcery")])
    # disc not on the sheet → counts as 0, still resolves (not unknown).
    pool, _parts, unknown = resolve_pool("blood sorcery", {}, idx)
    assert pool == 0 and unknown == []


def test_resolve_pool_adds_specialty_die_when_owned():
    idx = build_trait_index([("attr_strength", "Strength")], [("skill_brawl", "Brawl")])
    sheet = {"attr_strength": 3, "skill_brawl": 2,
             "specialties": [{"skill": "skill_brawl", "name": "Grappling"}]}
    pool, parts, unknown = resolve_pool("strength + brawl.grappling", sheet, idx)
    assert pool == 6   # 3 + 2 + 1 specialty die
    assert unknown == []
    assert any("spec" in lbl.lower() for lbl, _ in parts)


def test_resolve_pool_specialty_not_owned_flagged_no_die():
    idx = build_trait_index([("skill_brawl", "Brawl")])
    sheet = {"skill_brawl": 2, "specialties": []}
    pool, _parts, unknown = resolve_pool("brawl.kickboxing", sheet, idx)
    assert pool == 2   # no +1 — character lacks that specialty
    assert any("kickboxing" in u.lower() for u in unknown)


# ── Roll cog: trait index + embed builder (offline) ──────────────────────────

def test_roll_cog_trait_index_resolves_real_traits():
    from bot.cogs.roll import _TRAIT_INDEX
    assert _TRAIT_INDEX["strength"] == "attr_strength"
    assert _TRAIT_INDEX["brawl"] == "skill_brawl"
    assert _TRAIT_INDEX["blood sorcery"] == "disc_blood_sorcery"
    assert _TRAIT_INDEX["dominate"] == "disc_dominate"


def test_build_roll_embed_renders_messy_critical():
    from bot.cogs.roll import build_roll_embed
    # [10,10,7] normal + [10] hunger, diff 2 → 6 successes, messy critical.
    res = classify([10, 10, 7], [10], difficulty=2)
    e = build_roll_embed(res, title="Valeria",
                         pool_parts=[("Strength", 3), ("Brawl", 2)])
    assert "Messy Critical" in e.description
    names = [f.name for f in e.fields]
    assert "Dice" in names and "Hunger" in names and "Result" in names
    assert "Pool: Strength 3 + Brawl 2" in e.footer.text


def test_build_roll_embed_flags_unknown_traits():
    from bot.cogs.roll import build_roll_embed
    res = classify([8, 7], [], difficulty=0)
    e = build_roll_embed(res, title="Test", pool_parts=[("Strength", 3)],
                         unknown=["bogus"])
    assert "Unknown: bogus" in e.footer.text


# ── Willpower reroll + Rouse check ───────────────────────────────────────────

def test_reroll_failures_keeps_successes_and_rerolls_failures():
    # [8,5,4] normal — 8 is a success, 5 and 4 are failures.
    res, n = reroll_failures([8, 5, 4], [3], difficulty=0, count=3,
                             rng=random.Random(1))
    assert n == 2            # only the two failures were rerolled
    assert 8 in res.normal_dice   # the success survives


def test_reroll_failures_respects_count_cap():
    res, n = reroll_failures([1, 2, 3, 4], [], difficulty=0, count=3,
                             rng=random.Random(1))
    assert n == 3           # 4 failures, capped to 3


def test_reroll_failures_noop_when_all_succeed():
    res, n = reroll_failures([8, 9, 10], [], difficulty=0, count=3,
                             rng=random.Random(1))
    assert n == 0
    assert res.normal_dice == [10, 9, 8]


def test_rouse_check_counts_hunger_gain():
    rolls, gained = rouse_check(3, rng=random.Random(2))
    assert len(rolls) == 3
    assert gained == sum(1 for d in rolls if d < 6)


def test_rouse_check_single_die_by_default():
    rolls, gained = rouse_check(rng=random.Random(5))
    assert len(rolls) == 1
    assert gained in (0, 1)
