"""settings_enums.py — the single source for admin dropdowns + db re-exports."""
from web import db
from web import settings_enums as se


def test_option_lists_well_formed():
    for name in ("CREATION_MODE_OPTIONS", "RULESET_OPTIONS",
                 "RESONANCE_MODE_OPTIONS", "PROJECT_MODE_OPTIONS"):
        opts = getattr(se, name)
        assert opts, f"{name} is empty"
        assert all(o.get("value") and o.get("label") for o in opts), name
        # value/label/desc unique-enough — no duplicate values
        vals = [o["value"] for o in opts]
        assert len(vals) == len(set(vals)), f"{name} has duplicate values"


def test_derived_values_match_historical_shapes():
    assert se.CREATION_MODES == ("guided", "open")
    assert se.RULESETS == ("standard", "homebrew")
    assert se.RESONANCE_MODES == ("standard", "tattered_facade", "add_empty")
    assert se.PROJECT_MODES == {"nybn", "homebrew", "raw", "off"}
    assert isinstance(se.PROJECT_MODES, set)          # membership set, historically


def test_db_reexports_are_the_same_objects():
    # db.py imports these from settings_enums — importers like main.py / staff.py
    # that do `from ..db import PROJECT_MODES` must still get the one source.
    assert db.RULESETS is se.RULESETS
    assert db.RESONANCE_MODES is se.RESONANCE_MODES
    assert db.PROJECT_MODES is se.PROJECT_MODES
    assert db.CREATION_MODES is se.CREATION_MODES


def test_project_mode_raw_is_disabled():
    raw = next(o for o in se.PROJECT_MODE_OPTIONS if o["value"] == "raw")
    assert raw.get("disabled") is True
