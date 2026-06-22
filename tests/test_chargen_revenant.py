"""V5 RAW chargen validation for Revenants (NYbN house rules).

Revenants build like a mortal + add-ons: standard attribute/skill spreads, but
3 base Discipline dots = 2 across the family's Disciplines + 1 in a single
"domitor" Discipline (any). They also cannot take the Unbondable merit. The
family Bane (Severity 1) is applied at finalize, not validated here.
"""
from web.v5_traits import validate_chargen_raw, V5_ATTRIBUTES, V5_SKILLS

# Zantosa family Disciplines, straight from the seeded revenant_families data.
_ZANTOSA = ["Auspex", "Presence", "Protean"]


def _base_sheet():
    """Valid base allocation — 4/3/3/3/2/2/2/2/1 attrs + a Balanced skill
    distribution + the free specialties — with NO disciplines (caller adds)."""
    attr_keys = [k for _, t in V5_ATTRIBUTES for k, _ in t]
    skill_keys = [k for _, t in V5_SKILLS for k, _ in t]
    sheet: dict = {}
    for k, v in zip(attr_keys, [4, 3, 3, 3, 2, 2, 2, 2, 1]):
        sheet[k] = v
    for k, v in zip(skill_keys, [3, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1]):
        sheet[k] = v
    sheet["skill_spread"] = "balanced"
    _free = 1 + sum(1 for k in ("skill_academics", "skill_science", "skill_craft",
                                "skill_performance") if sheet.get(k, 0) > 0)
    _dotted = [k for k in skill_keys if sheet.get(k, 0) > 0]
    sheet["specialties"] = [{"skill": _dotted[i % len(_dotted)], "name": f"Spec {i + 1}"}
                            for i in range(_free)]
    return sheet


def _revenant_sheet():
    """RAW-valid Revenant — 2 dots in family (Auspex) + 1 domitor (Dominate)."""
    s = _base_sheet()
    s["disc_auspex"] = 2        # family
    s["disc_dominate"] = 1      # domitor — not a Zantosa Discipline
    return s


def _v(sheet, **kw):
    return validate_chargen_raw(sheet, character_type="revenant",
                                family_disciplines=_ZANTOSA, **kw)


def test_valid_revenant_passes():
    assert _v(_revenant_sheet()) == []


def test_too_many_discipline_dots_rejected():
    s = _revenant_sheet()
    s["disc_presence"] = 1      # family 3 + domitor 1 -> 4 total
    assert any("Revenant" in e for e in _v(s))


def test_too_few_family_dots_rejected():
    s = _base_sheet()
    s["disc_auspex"] = 1        # only 1 in family
    s["disc_dominate"] = 2      # 2 in a single non-family Discipline
    assert any("Revenant" in e for e in _v(s))


def test_two_separate_domitor_disciplines_rejected():
    s = _base_sheet()
    s["disc_auspex"] = 1
    s["disc_dominate"] = 1
    s["disc_celerity"] = 1      # two different non-family Disciplines
    assert any("Revenant" in e for e in _v(s))


def test_three_dots_all_in_family_allowed():
    """The domitor dot may coincide with a family Discipline (overlap edge)."""
    s = _base_sheet()
    s["disc_auspex"] = 2
    s["disc_presence"] = 1      # all 3 within Zantosa's Disciplines
    assert _v(s) == []


def test_unbondable_merit_blocked():
    s = _revenant_sheet()
    s["merits"] = [{"name": "Unbondable", "dots": 2}]
    assert any("Unbondable" in e for e in _v(s))


def test_family_unknown_enforces_only_total():
    """With no family Disciplines supplied, only the 3-dot total is enforced."""
    s = _revenant_sheet()                                          # total 3
    assert validate_chargen_raw(s, character_type="revenant") == []
    s["disc_presence"] = 1                                         # total 4
    assert validate_chargen_raw(s, character_type="revenant") != []


# ── Route: the family Bane (Severity 1) is auto-applied on save ───────────────

def test_revenant_draft_applies_family_bane(player):
    """Saving a Revenant draft auto-grants the family Bane as a free
    src='revenant_bane' flaw at Bane Severity 1 (NYbN: all revenants are Sev 1)."""
    from web.db import get_db, get_character, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, revenants_enabled=1)
    r = player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "autosave": "1", "as_draft": "1",
        "name": "Rev Bane WIP", "character_type": "revenant",
        "revenant_family": "Zantosa",
    }, follow_redirects=False)
    assert r.status_code == 200, r.text[:300]
    did = r.json()["draft_id"]
    with get_db() as conn:
        sheet = (get_character(conn, did) or {}).get("sheet_json") or {}
        flaws = sheet.get("flaws") or []
        assert any(f.get("src") == "revenant_bane"
                   and f.get("name") == "Zantosa Bane" for f in flaws), flaws
        assert sheet.get("bane_severity") == 1
        conn.execute("DELETE FROM characters WHERE id=?", (did,))
        conn.commit()
