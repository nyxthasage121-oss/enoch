"""Cost-formula regression tests for web/xp_rules.calculate_cost.

Locks the house XP table (packages/rules/xp_costs.json) against drift and
guards the Skill 1->2 fix: min_dots was 2, which left a gap between
New Skill (0->1) and Skill (>=2) so a rating-1 skill couldn't be raised.
"""
import pytest
from web.xp_rules import calculate_cost


@pytest.mark.parametrize("category,cur,new,expected", [
    ("Attribute",            1, 2, 10),   # 2x5
    ("Attribute",            1, 3, 25),   # 2x5 + 3x5 (progressive)
    ("Skill",                1, 2, 6),    # 2x3  <- regression guard
    ("Skill",                2, 3, 9),    # 3x3
    ("Skill",                1, 3, 15),   # 2x3 + 3x3 (progressive)
    ("New Skill",            0, 1, 3),    # flat
    ("Specialty",            0, 1, 3),    # flat
    ("Clan Discipline",      1, 2, 10),   # 2x5
    ("Other Discipline",     1, 2, 14),   # 2x7
    ("Caitiff Discipline",   1, 2, 12),   # 2x6
    ("Advantage",            1, 3, 6),    # 2 dots x 3 (flat per dot)
    ("Blood Potency",        1, 2, 20),   # 2x10
    ("Humanity",             6, 7, 14),   # 7x2 (single dot)
    ("Blood Sorcery Ritual", 2, 3, 9),    # level x3 (flat per level)
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


def test_skill_zero_to_one_still_routes_to_new_skill():
    """0->1 stays New Skill's job — the Skill category still rejects current=0
    so the two categories don't overlap."""
    cost, err = calculate_cost("Skill", 0, 1)
    assert err is not None
    assert cost == 0
