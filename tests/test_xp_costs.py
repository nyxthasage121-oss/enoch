"""Cost-formula regression tests for web/xp_rules.calculate_cost.

Locks the house XP table (packages/rules/xp_costs.json) against drift. The
single Skill category covers the full 0->5 range at new rating x 3 (the old
separate flat "New Skill" 0->1 line was merged away — x3 is correct even for
the first dot, since 1 x 3 = 3).
"""
import pytest
from web.xp_rules import calculate_cost


@pytest.mark.parametrize("category,cur,new,expected", [
    ("Attribute",            1, 2, 10),   # 2x5
    ("Attribute",            1, 3, 25),   # 2x5 + 3x5 (progressive)
    ("Skill",                1, 2, 6),    # 2x3  <- regression guard
    ("Skill",                2, 3, 9),    # 3x3
    ("Skill",                1, 3, 15),   # 2x3 + 3x3 (progressive)
    ("Skill",                0, 1, 3),    # 1x3 — a brand-new skill, uniform x3
    ("Skill",                0, 3, 18),   # 1x3 + 2x3 + 3x3 — learned from scratch
    ("Specialty",            0, 1, 3),    # flat
    ("Clan Discipline",      1, 2, 10),   # 2x5
    ("Other Discipline",     1, 2, 14),   # 2x7
    ("Caitiff Discipline",   1, 2, 12),   # 2x6
    ("Ghoul Discipline",     0, 1, 10),   # flat (ghoul's first Discipline dot)
    ("Advantage",            1, 3, 6),    # 2 dots x 3 (flat per dot)
    ("Blood Potency",        1, 2, 20),   # 2x10
    ("Humanity",             6, 7, 14),   # 7x2 (single dot)
    ("Blood Sorcery Ritual", 2, 3, 9),    # level x3 (flat per level)
    ("Blood Sorcery Ritual", 0, 1, 3),    # level 1 from scratch (was unbuyable)
    ("Blood Sorcery Ritual", 0, 3, 9),    # level 3 from scratch
    ("Thin-Blood Alchemy Formula", 0, 2, 6),  # level 2 from scratch
])
def test_calculate_cost_matches_house_table(category, cur, new, expected):
    cost, err = calculate_cost(category, cur, new)
    assert err is None, f"{category} {cur}->{new} unexpectedly errored: {err}"
    assert cost == expected, f"{category} {cur}->{new}: got {cost}, want {expected}"


def test_skill_can_be_raised_from_rating_one():
    """Regression: a rating-1 skill must be raisable to 2. The 1->2 raise
    used to fail because Skill.min_dots was 2 (New Skill only does 0->1)."""
    cost, err = calculate_cost("Skill", 1, 2)
    assert err is None
    assert cost == 6


def test_skill_zero_to_one_is_uniform_new_times_three():
    """A brand-new skill (0->1) is bought through the single Skill category at
    new rating x 3 (= 3). The separate flat 'New Skill' category was merged
    away — x3 is correct even for the first dot."""
    cost, err = calculate_cost("Skill", 0, 1)
    assert err is None
    assert cost == 3


def test_ghoul_discipline_is_first_dot_only():
    """Ghoul Discipline is a flat 10 XP buy for the first dot only (0->1).
    Anything past the first dot must be rejected (ghouls can't keep buying
    a Discipline up at the flat ghoul rate)."""
    assert calculate_cost("Ghoul Discipline", 0, 1) == (10, None)
    cost, err = calculate_cost("Ghoul Discipline", 1, 2)
    assert err is not None and cost == 0


def test_rituals_and_formulas_buyable_from_scratch():
    """Regression: level_multiplier categories had min_dots=1, making a
    level-1 ritual unbuyable (current would need to be >=1 AND <1). They're
    learned 0->N now; cost is level x 3 regardless of 'current'."""
    assert calculate_cost("Blood Sorcery Ritual", 0, 1) == (3, None)
    assert calculate_cost("Blood Sorcery Ritual", 0, 5) == (15, None)
    assert calculate_cost("Thin-Blood Alchemy Formula", 0, 1) == (3, None)
