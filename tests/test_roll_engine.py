"""Unit tests for the V5 dice roll engine (core/dice.py).

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

from core.dice import (  # noqa: E402
    classify, roll_pool, build_trait_index, resolve_pool, apply_specialty,
    reroll_failures, rouse_check, blood_surge_bonus, mend_amount, willpower_recovery,
    bane_severity, frenzy_pool, remorse_pool, hunt_outcome, hunt_slake,
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


def test_apply_specialty_adds_die_when_owned():
    specs = [{"skill": "skill_brawl", "name": "Grappling"}]
    pool, parts, unknown = apply_specialty(5, [("Brawl", 2)], [],
                                           "skill_brawl:Grappling", specs)
    assert pool == 6
    assert unknown == []
    assert any("spec" in lbl.lower() for lbl, _ in parts)


def test_apply_specialty_matches_bare_name():
    specs = [{"skill": "skill_brawl", "name": "Grappling"}]
    pool, _parts, unknown = apply_specialty(5, [], [], "Grappling", specs)
    assert pool == 6 and unknown == []


def test_apply_specialty_unowned_flagged_no_die():
    pool, _parts, unknown = apply_specialty(5, [], [], "skill_brawl:Kickboxing", [])
    assert pool == 5
    assert any("kickboxing" in u.lower() for u in unknown)


def test_apply_specialty_none_is_noop():
    pool, parts, unknown = apply_specialty(5, [("Brawl", 2)], [], None, [])
    assert pool == 5 and parts == [("Brawl", 2)] and unknown == []


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


def test_build_roll_embed_shows_blood_surge_note():
    from bot.cogs.roll import build_roll_embed
    res = classify([8, 7, 6], [], difficulty=2)
    e = build_roll_embed(res, title="Valeria",
                         note="+3 dice · Rouse 4 → +1 Hunger → 2/5")
    surge = next((f for f in e.fields if f.name == "Blood Surge"), None)
    assert surge is not None and "+3 dice" in surge.value


def test_specialty_autocomplete_lists_and_filters_owned(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import bot.cogs.roll as rollcog

    async def fake_chars(_discord_id):
        return [{"id": 7, "name": "Valeria", "is_approved": True}]

    async def fake_char(_cid):
        return {"id": 7, "name": "Valeria", "sheet_json": {"specialties": [
            {"skill": "skill_brawl", "name": "Grappling"},
            {"skill": "skill_academics", "name": "Occult Lore"},
        ]}}

    monkeypatch.setattr(rollcog, "get_player_characters", fake_chars)
    monkeypatch.setattr(rollcog, "get_character", fake_char)

    cog = rollcog.RollCog(bot=None)
    interaction = SimpleNamespace(user=SimpleNamespace(id=111),
                                  namespace=SimpleNamespace(character=None))

    everything = asyncio.run(cog._specialty_autocomplete(interaction, ""))
    values = {c.value for c in everything}
    assert "skill_brawl:Grappling" in values
    assert "skill_academics:Occult Lore" in values
    assert any("Brawl" in c.name and "Grappling" in c.name for c in everything)

    # Typed text filters the suggestions.
    filtered = asyncio.run(cog._specialty_autocomplete(interaction, "grap"))
    assert [c.value for c in filtered] == ["skill_brawl:Grappling"]


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


def test_blood_surge_bonus_by_blood_potency():
    # +1 at BP0, +2 at 1-2, +3 at 3-4, +4 at 5-6, +5 at 7-8, +6 at 9-10.
    assert blood_surge_bonus(0) == 1
    assert blood_surge_bonus(1) == 2
    assert blood_surge_bonus(2) == 2
    assert blood_surge_bonus(3) == 3
    assert blood_surge_bonus(4) == 3
    assert blood_surge_bonus(5) == 4
    assert blood_surge_bonus(7) == 5
    assert blood_surge_bonus(9) == 6
    assert blood_surge_bonus(10) == 6


def test_mend_amount_by_blood_potency():
    # 1 at BP0-1, 2 at 2-3, 3 at 4-7, 4 at 8-9, 5 at 10.
    assert mend_amount(0) == 1
    assert mend_amount(1) == 1
    assert mend_amount(2) == 2
    assert mend_amount(3) == 2
    assert mend_amount(4) == 3
    assert mend_amount(7) == 3
    assert mend_amount(8) == 4
    assert mend_amount(10) == 5


def test_willpower_recovery_is_higher_of_composure_resolve():
    assert willpower_recovery(2, 3) == 3
    assert willpower_recovery(4, 1) == 4
    assert willpower_recovery(0, 0) == 0


def test_bane_severity_by_blood_potency():
    # V5 Corebook p.216: 0 at BP 0, else ceil(BP / 2) + 1.
    assert bane_severity(0) == 0
    assert bane_severity(1) == 2
    assert bane_severity(2) == 2
    assert bane_severity(3) == 3
    assert bane_severity(4) == 3
    assert bane_severity(6) == 4
    assert bane_severity(8) == 5
    assert bane_severity(10) == 6


def test_frenzy_pool_is_current_willpower():
    # Resolve 3 + Composure 2 = 5 Willpower, minus 1 sup + 1 agg damage = 3.
    assert frenzy_pool(3, 2) == 5
    assert frenzy_pool(3, 2, willpower_sup=1, willpower_agg=1) == 3
    assert frenzy_pool(1, 1, willpower_sup=5) == 0   # floored at 0


def test_remorse_pool_unstained_empty_humanity_boxes():
    # Humanity 7 → 3 empty; minus Stains.
    assert remorse_pool(7, 1) == 2
    assert remorse_pool(7, 2) == 1
    assert remorse_pool(7, 5) == 1   # all empties stained → minimum 1
    assert remorse_pool(5, 1) == 4


# ── Hunting (feeding rolls) ──────────────────────────────────────────────────

def test_hunt_outcome_maps_each_roll_outcome():
    # Critical → clean; messy critical → messy; plain win → success.
    assert hunt_outcome(classify([10, 10], [], difficulty=2)) == "clean"
    assert hunt_outcome(classify([10], [10], difficulty=2)) == "messy_critical"
    assert hunt_outcome(classify([8, 7], [], difficulty=1)) == "success"
    # Bestial failure (a 1 on a Hunger die on a loss) → bestial_failure.
    assert hunt_outcome(classify([3, 2], [1], difficulty=3)) == "bestial_failure"


def test_hunt_outcome_plain_failure_is_not_logged():
    # A plain miss (no Hunger 1) turned up no prey → nothing to log.
    assert hunt_outcome(classify([5, 4], [3], difficulty=3)) is None
    assert hunt_outcome(classify([2, 2], [], difficulty=2)) is None


def test_hunt_slake_scales_with_margin_capped_by_blood_quality():
    # Bare win (margin 0) slakes 1.
    assert hunt_slake(classify([8], [], difficulty=1), blood_quality=5) == 1
    # Margin 2 → slakes 3, but a poor site (quality 2) caps it at 2.
    big = classify([8, 7, 6, 9], [], difficulty=2)   # 4 succ vs 2 → margin 2
    assert big.margin == 2
    assert hunt_slake(big, blood_quality=5) == 3
    assert hunt_slake(big, blood_quality=2) == 2


def test_hunt_slake_zero_on_failure_and_bestial():
    assert hunt_slake(classify([5, 4], [3], difficulty=3), blood_quality=5) == 0
    assert hunt_slake(classify([3], [1], difficulty=3), blood_quality=5) == 0


def test_build_hunt_embed_clean_feed():
    from bot.cogs.roll import build_hunt_embed
    res = classify([8, 7], [], difficulty=1)   # success
    e = build_hunt_embed(res, character="Valeria", site="The Velvet Rope",
                         outcome="success", slaked=1, new_hunger=2,
                         pool_parts=[("Manipulation", 3), ("Subterfuge", 2)],
                         blood_quality=3)
    assert "Valeria hunts" in e.title and "Velvet Rope" in e.title
    fed = next(f for f in e.fields if f.name == "Fed").value
    assert "Slaked 1 Hunger" in fed and "2/5" in fed
    assert "Blood quality 3" in e.footer.text


def test_build_hunt_embed_miss_takes_no_blood():
    from bot.cogs.roll import build_hunt_embed
    res = classify([5, 4], [3], difficulty=3)   # plain failure
    e = build_hunt_embed(res, character="Marcus", site="Back Alley",
                         outcome=None, slaked=0, new_hunger=3)
    fed = next(f for f in e.fields if f.name == "Fed").value
    assert "No blood taken" in fed


def test_hunt_dc_helper_chasse_and_default():
    from bot.cogs.roll import _hunt_dc
    site = {"predator_dcs": {"Siren": 3}, "effective_dcs": {"Siren": 2}}
    # Owner gets the Chasse-reduced DC; outsider gets the base.
    assert _hunt_dc(site, "Siren", owns=True, override=None) == (2, 3, False)
    assert _hunt_dc(site, "Siren", owns=False, override=None) == (3, 3, False)
    # A staff override always wins.
    assert _hunt_dc(site, "Siren", owns=True, override=5) == (5, 3, False)
    # No DC for this predator type → the standard default, flagged.
    dc, base, defaulted = _hunt_dc(site, "Alleycat", owns=False, override=None)
    assert base is None and defaulted is True and dc == 2
