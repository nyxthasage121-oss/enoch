"""Thin-blood creation rule: thin-blood-specific Merits and Flaws are a separate
1:1 balanced set (each tb Merit funded by a tb Flaw) and are free of the normal
Advantages pool / flaw cap. Enforced in validate_chargen_raw."""
from web.v5_traits import validate_chargen_raw, MERIT_CATALOG, FLAW_CATALOG

_TB_MERITS = [m["name"] for m in MERIT_CATALOG if m.get("restriction") == "thinblood"]
_TB_FLAWS = [f["name"] for f in FLAW_CATALOG if f.get("restriction") == "thinblood"]


def test_catalog_has_thinblood_traits():
    assert len(_TB_MERITS) >= 3 and len(_TB_FLAWS) >= 3


def test_thinblood_merits_must_be_matched_by_flaws():
    over = {
        "merits": [{"name": _TB_MERITS[0], "dots": 1}, {"name": _TB_MERITS[1], "dots": 1}],
        "flaws":  [{"name": _TB_FLAWS[0], "dots": 1}],
    }  # 2 tb merits, 1 tb flaw → unbalanced
    errs = validate_chargen_raw(over, clan="thin-blood", advantage_pool=7,
                                flaw_cap=2, flaw_min=0)
    assert any("Thin-Blood Merits" in e for e in errs)

    ok = {
        "merits": [{"name": _TB_MERITS[0], "dots": 1}],
        "flaws":  [{"name": _TB_FLAWS[0], "dots": 1}],
    }  # 1:1 balanced
    assert not any("Thin-Blood Merits" in e
                   for e in validate_chargen_raw(ok, clan="thin-blood",
                                                 advantage_pool=7, flaw_cap=2, flaw_min=0))


def test_thinblood_traits_are_free_of_pool_and_cap():
    sheet = {
        "merits":      [{"name": n, "dots": 1} for n in _TB_MERITS[:3]],
        "flaws":       [{"name": n, "dots": 1} for n in _TB_FLAWS[:3]],
        "backgrounds": [{"name": "Resources", "dots": 7}],   # fills the pool exactly
    }
    errs = validate_chargen_raw(sheet, clan="thin-blood", advantage_pool=7,
                                flaw_cap=0, flaw_min=0)
    assert not any("Advantages" in e for e in errs)    # tb merits don't eat the pool
    assert not any("Flaws total" in e for e in errs)   # tb flaws don't hit the cap
