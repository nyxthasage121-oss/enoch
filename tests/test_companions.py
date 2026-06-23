"""Companions (Retainers & Mawlas) — issue #1.

Covers the V5 Mortals-Template validation for Retainers (a Retainer's stat
block must match the template its background rating selects: ● Weak / ●● Average
/ ●●● Gifted) and the companions DB layer (create / list / update / delete +
cascade on character delete).
"""
from web.v5_traits import (MORTAL_TEMPLATES, RETAINER_DOTS_TO_TEMPLATE,
                           _ATTR_KEYS, _SKILL_KEYS, validate_retainer_template)


def _template_sheet(template: str) -> dict:
    """Build a stat block that exactly satisfies the given Mortals Template."""
    tpl = MORTAL_TEMPLATES[template]
    sheet: dict = {}
    for key, val in zip(_ATTR_KEYS, sorted(tpl["attributes"], reverse=True)):
        sheet[key] = val
    flat: list[int] = []
    for lvl, n in sorted(tpl["skills"].items(), reverse=True):
        flat += [lvl] * n
    for key, val in zip(_SKILL_KEYS, flat):
        sheet[key] = val
    sheet["specialties"] = [{"skill": _SKILL_KEYS[0], "name": f"Spec {i}"}
                            for i in range(tpl["specialties"])]
    return sheet


# ── Template data sanity ─────────────────────────────────────────────────────

def test_dots_map_to_templates():
    assert RETAINER_DOTS_TO_TEMPLATE == {1: "weak", 2: "average", 3: "gifted"}


def test_attribute_multisets_total_nine():
    for slug, tpl in MORTAL_TEMPLATES.items():
        assert len(tpl["attributes"]) == 9, slug


def test_skill_counts_match_chart():
    # The four V5 Mortals Templates, verbatim from Core.
    assert MORTAL_TEMPLATES["weak"]["skills"] == {2: 3, 1: 5}
    assert MORTAL_TEMPLATES["average"]["skills"] == {3: 3, 2: 4, 1: 5}
    assert MORTAL_TEMPLATES["gifted"]["skills"] == {4: 2, 3: 4, 2: 4, 1: 4}
    assert MORTAL_TEMPLATES["deadly"]["skills"] == {5: 1, 4: 3, 3: 5, 2: 6}


# ── Validator ────────────────────────────────────────────────────────────────

def test_every_template_self_validates():
    for slug in MORTAL_TEMPLATES:
        assert validate_retainer_template(_template_sheet(slug), slug) == [], slug


def test_unknown_template_rejected():
    assert validate_retainer_template({}, "nope")


def test_wrong_attributes_rejected():
    sheet = _template_sheet("average")
    sheet["attr_strength"] = 5  # breaks the multiset
    assert any("Attributes" in e for e in validate_retainer_template(sheet, "average"))


def test_wrong_skills_rejected():
    sheet = _template_sheet("weak")
    for k in _SKILL_KEYS:               # bump a 1-dot skill to 3
        if sheet.get(k) == 1:
            sheet[k] = 3
            break
    assert any("Skills" in e for e in validate_retainer_template(sheet, "weak"))


def test_mortal_with_discipline_rejected():
    sheet = _template_sheet("average")
    sheet["disc_dominate"] = 1
    assert any("Discipline" in e
               for e in validate_retainer_template(sheet, "average", is_ghoul=False))


def test_ghoul_needs_exactly_one_discipline():
    sheet = _template_sheet("average")
    assert any("Discipline" in e
               for e in validate_retainer_template(sheet, "average", is_ghoul=True))
    sheet["disc_dominate"] = 1
    assert validate_retainer_template(sheet, "average", is_ghoul=True) == []


def test_flaw_cap_enforced():
    sheet = _template_sheet("weak")        # Weak allows no Flaws
    sheet["flaws"] = [{"name": "Enemy", "dots": 1}]
    assert any("Flaw" in e for e in validate_retainer_template(sheet, "weak"))


def test_advantage_points_enforced():
    sheet = _template_sheet("average")     # Average allows up to 3 Advantage pts
    sheet["merits"] = [{"name": "Resources", "dots": 5}]
    assert any("Advantages" in e for e in validate_retainer_template(sheet, "average"))


def test_src_tagged_advantages_are_free():
    # A clan/predator-granted (src-tagged) entry doesn't count against the pool.
    sheet = _template_sheet("weak")
    sheet["merits"] = [{"name": "Domitor's Gift", "dots": 3, "src": "ghoul"}]
    assert validate_retainer_template(sheet, "weak") == []


# ── DB layer (needs the app booted so migration 047 has run) ─────────────────

def test_companion_crud_round_trip(_client):
    from web.db import (get_db, upsert_player, create_character,
                        create_companion, list_companions, get_companion,
                        update_companion, delete_companion, delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="770000770000770000", username="CompTest")
        ch = create_character(conn, discord_id="770000770000770000",
                              name="Companion Probe", clan="ventrue")
    cid = ch["id"]
    try:
        with get_db() as conn:
            comp = create_companion(
                conn, parent_character_id=cid, kind="retainer", name="Marcus",
                dots=2, template="average", concept="Driver",
                sheet_json=_template_sheet("average"))
            comp_id = comp["id"]
            assert comp["name"] == "Marcus"
            assert comp["sheet_json"]["attr_strength"] >= 1   # JSON round-tripped
            assert comp["is_ghoul"] is False

            assert len(list_companions(conn, cid)) == 1
            assert list_companions(conn, cid, kind="retainer")[0]["kind"] == "retainer"
            assert list_companions(conn, cid, kind="mawla") == []

            update_companion(conn, comp_id, name="Marcus Vale", is_ghoul=True)
            got = get_companion(conn, comp_id)
            assert got["name"] == "Marcus Vale" and got["is_ghoul"] is True

            delete_companion(conn, comp_id)
            assert get_companion(conn, comp_id) is None
            conn.commit()
    finally:
        with get_db() as conn:
            delete_character(conn, cid)
            conn.commit()


def test_companions_cascade_on_character_delete(_client):
    from web.db import (get_db, upsert_player, create_character,
                        create_companion, list_companions, delete_character)
    with get_db() as conn:
        upsert_player(conn, discord_id="771111771111771111", username="CascadeTest")
        ch = create_character(conn, discord_id="771111771111771111",
                              name="Cascade Probe", clan="brujah")
        cid = ch["id"]
        create_companion(conn, parent_character_id=cid, kind="mawla",
                         name="Old Bishop", dots=3, clan="lasombra")
        assert len(list_companions(conn, cid)) == 1
        delete_character(conn, cid)
        assert list_companions(conn, cid) == []
        conn.commit()


# ── Player routes (char 1 = Valeria Morano, owned by the dev TestPlayer) ─────

def test_companions_page_loads(player):
    r = player.get("/characters/1/companions")
    assert r.status_code == 200
    assert "Retainers" in r.text and "Build a Retainer" in r.text


def test_create_and_delete_retainer_via_route(player):
    import json as _json
    from web.db import get_db, list_companions
    r = player.post("/characters/1/companions", data={
        "_csrf": "dev-csrf-token", "kind": "retainer", "name": "QA Retainer",
        "dots": "2", "concept": "driver",
        "sheet_json": _json.dumps(_template_sheet("average")),
    }, follow_redirects=False)
    assert r.status_code == 303
    with get_db() as conn:
        mine = [c for c in list_companions(conn, 1) if c["name"] == "QA Retainer"]
    assert len(mine) == 1 and mine[0]["template"] == "average"
    cid = mine[0]["id"]
    assert "QA Retainer" in player.get("/characters/1/companions").text

    r2 = player.post(f"/companions/{cid}/delete",
                     data={"_csrf": "dev-csrf-token"}, follow_redirects=False)
    assert r2.status_code == 303
    with get_db() as conn:
        assert not [c for c in list_companions(conn, 1) if c["id"] == cid]


def test_invalid_retainer_rejected_via_route(player):
    import json as _json
    from web.db import get_db, list_companions
    bad = _template_sheet("average")
    bad["attr_strength"] = 5                      # breaks the multiset
    r = player.post("/characters/1/companions", data={
        "_csrf": "dev-csrf-token", "kind": "retainer", "name": "QA Bad Spread",
        "dots": "2", "sheet_json": _json.dumps(bad),
    }, follow_redirects=False)
    assert r.status_code == 200                   # re-rendered with errors
    assert "Couldn't save" in r.text
    with get_db() as conn:
        assert not [c for c in list_companions(conn, 1) if c["name"] == "QA Bad Spread"]


def test_companions_ownership_enforced(player):
    assert player.get("/characters/999999/companions").status_code == 404


# ── Mawla (Kindred mentor) ───────────────────────────────────────────────────

def _mawla_sheet(clan: str) -> dict:
    """A standard-spread Kindred stat block for the given clan (Balanced skills,
    in-clan 2 + 1 Disciplines)."""
    from web.v5_traits import (_ATTR_KEYS, _SKILL_KEYS, V5_ATTRIBUTE_SPREAD,
                               V5_SKILL_SPREADS, CLAN_DISCIPLINES)
    sheet: dict = {}
    for k, v in zip(_ATTR_KEYS, sorted(V5_ATTRIBUTE_SPREAD, reverse=True)):
        sheet[k] = v
    flat: list[int] = []
    for lvl, n in sorted(V5_SKILL_SPREADS["balanced"]["levels"].items(), reverse=True):
        flat += [lvl] * n
    for k, v in zip(_SKILL_KEYS, flat):
        sheet[k] = v
    discs = CLAN_DISCIPLINES[clan]
    sheet[discs[0]] = 2
    sheet[discs[1]] = 1
    return sheet


def test_mawla_valid_passes():
    from web.v5_traits import validate_mawla_kindred
    assert validate_mawla_kindred(_mawla_sheet("ventrue"), "ventrue") == []


def test_mawla_bad_attributes_rejected():
    from web.v5_traits import validate_mawla_kindred
    s = _mawla_sheet("ventrue")
    s["attr_strength"] = 5
    assert validate_mawla_kindred(s, "ventrue")


def test_mawla_out_of_clan_discipline_rejected():
    from web.v5_traits import validate_mawla_kindred, CLAN_DISCIPLINES, _disc_keys
    s = _mawla_sheet("ventrue")
    out = next(k for k in _disc_keys() if k not in set(CLAN_DISCIPLINES["ventrue"]))
    s[out] = 1
    assert validate_mawla_kindred(s, "ventrue")


def test_mawla_creation_disabled(player):
    """Mawla creation is on hold (coming soon) — a POST must not create one."""
    import json as _json
    from web.db import get_db, list_companions
    r = player.post("/characters/1/companions", data={
        "_csrf": "dev-csrf-token", "kind": "mawla", "name": "QA Bishop",
        "clan": "ventrue", "dots": "3",
        "sheet_json": _json.dumps(_mawla_sheet("ventrue")),
    }, follow_redirects=False)
    assert r.status_code == 200            # re-rendered, not created/redirected
    assert "coming soon" in r.text.lower()
    with get_db() as conn:
        assert not [c for c in list_companions(conn, 1) if c["name"] == "QA Bishop"]
