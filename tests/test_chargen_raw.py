"""Tests for V5 RAW chargen validation (attribute + skill priority spreads).

The validator checks the BASE allocation — a trait's dots BEFORE starting-XP
purchases — so XP buys (folded into the trait value, ledgered in `xp_buys`)
must be subtracted back out before comparing to the spread.
"""


def _valid_sheet():
    """A RAW-valid base allocation: the 4/3/3/3/2/2/2/2/1 attribute spread and a
    Balanced skill distribution (three 3s, five 2s, seven 1s)."""
    from web.v5_traits import V5_ATTRIBUTES, V5_SKILLS
    attr_keys = [k for _, t in V5_ATTRIBUTES for k, _ in t]
    skill_keys = [k for _, t in V5_SKILLS for k, _ in t]
    sheet: dict = {}
    for k, v in zip(attr_keys, [4, 3, 3, 3, 2, 2, 2, 2, 1]):
        sheet[k] = v
    balanced = [3, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1]  # 3×3, 5×2, 7×1
    for k, v in zip(skill_keys, balanced):
        sheet[k] = v
    sheet["skill_spread"] = "balanced"
    sheet["backgrounds"] = [{"name": "Allies", "dots": 3}, {"name": "Resources", "dots": 2}]
    sheet["merits"] = [{"name": "Iron Will", "dots": 2}]   # 7 advantage dots total
    sheet["flaws"] = [{"name": "Enemy", "dots": 1}, {"name": "Disliked", "dots": 1}]
    return sheet


def test_valid_chargen_passes():
    from web.v5_traits import validate_chargen_raw
    assert validate_chargen_raw(_valid_sheet()) == []


def test_bad_attribute_spread_rejected():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["attr_strength"] = 5  # spread becomes 5/3/3/3/2/2/2/2/1 — not RAW
    errs = validate_chargen_raw(s)
    assert any("Attributes" in e for e in errs)


def test_bad_skill_allocation_rejected():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["skill_athletics"] = 4  # Balanced has no 4 — breaks the distribution
    errs = validate_chargen_raw(s)
    assert any("Skill" in e for e in errs)


def test_xp_buys_are_subtracted_to_base():
    """A trait raised by starting XP keeps its BASE within the spread."""
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["attr_dexterity"] = 4  # final 4 (capped at creation) ...
    s["xp_buys"] = [  # ... bought up one dot from base 3 → base spread still valid
        {"cat": "attr", "key": "attr_dexterity", "label": "Dexterity", "cost": 20},
    ]
    assert validate_chargen_raw(s) == []


def test_skill_spread_choice_must_match_allocation():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["skill_spread"] = "specialist"  # allocation is Balanced, not Specialist
    errs = validate_chargen_raw(s)
    assert any("Specialist" in e or "Skill" in e for e in errs)


def test_base_trait_value_helper():
    from web.v5_traits import base_trait_value
    sheet = {"attr_strength": 4, "xp_buys": [
        {"cat": "attr", "key": "attr_strength"},
        {"cat": "attr", "key": "attr_strength"},
    ]}
    assert base_trait_value(sheet, "attr_strength") == 2   # 4 final − 2 bought
    assert base_trait_value(sheet, "attr_dexterity") == 0  # absent → 0


def test_in_clan_disciplines_pass():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["disc_celerity"] = 2  # Brujah in-clan = Celerity / Potence / Presence
    s["disc_presence"] = 1
    assert validate_chargen_raw(s, character_type="kindred", clan="brujah") == []


def test_out_of_clan_base_discipline_rejected():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["disc_dominate"] = 2  # Dominate is NOT in-clan for Brujah
    errs = validate_chargen_raw(s, character_type="kindred", clan="brujah")
    assert any("in-clan" in e.lower() for e in errs)


def test_caitiff_any_discipline_ok():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["disc_dominate"] = 2
    s["disc_auspex"] = 1
    assert validate_chargen_raw(s, character_type="kindred", clan="caitiff") == []


def test_xp_bought_out_of_clan_discipline_ok():
    """Out-of-clan Disciplines are legal when bought with starting XP — the
    base (pre-XP) value is 0, so the in-clan rule doesn't apply."""
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["disc_dominate"] = 2  # final 2, but...
    s["xp_buys"] = [        # ...both dots came from XP → base 0
        {"cat": "disc", "key": "disc_dominate"},
        {"cat": "disc", "key": "disc_dominate"},
    ]
    assert validate_chargen_raw(s, character_type="kindred", clan="brujah") == []


def test_non_kindred_skips_discipline_check():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["disc_dominate"] = 2
    assert validate_chargen_raw(s, character_type="mortal", clan="") == []


def test_valid_advantages_pass():
    from web.v5_traits import validate_chargen_raw
    assert validate_chargen_raw(
        _valid_sheet(), character_type="kindred", clan="brujah",
        advantage_pool=7, flaw_cap=2) == []


def test_advantages_over_pool_rejected():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["backgrounds"] = [{"name": "Allies", "dots": 5}, {"name": "Resources", "dots": 5}]  # 10
    errs = validate_chargen_raw(s, character_type="kindred", clan="brujah",
                                advantage_pool=7, flaw_cap=2)
    assert any("Advantages" in e for e in errs)


def test_flaw_minimum_enforced():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["flaws"] = [{"name": "Enemy", "dots": 1}]  # only 1 dot < 2
    errs = validate_chargen_raw(s, character_type="kindred", clan="brujah",
                                advantage_pool=7, flaw_cap=2)
    assert any("Flaw" in e for e in errs)


def test_auto_granted_flaws_dont_count_to_budget():
    """src-tagged entries (clan bane / predator) are free — they don't count
    toward the advantage pool or the flaw cap/minimum."""
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["flaws"] = [
        {"name": "Enemy", "dots": 2},                          # player flaws = 2
        {"name": "Repulsive", "dots": 2, "src": "clan_bane"},  # free, not counted
    ]
    assert validate_chargen_raw(s, character_type="kindred", clan="brujah",
                                advantage_pool=7, flaw_cap=2) == []


def test_nothing_at_five_at_creation():
    from web.v5_traits import validate_chargen_raw
    s = _valid_sheet()
    s["disc_celerity"] = 5  # in-clan for Brujah, but a 5 isn't allowed at creation
    errs = validate_chargen_raw(s, character_type="kindred", clan="brujah")
    assert any("5" in e for e in errs)
