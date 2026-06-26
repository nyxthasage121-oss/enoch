"""Sheet-independent (Inconnu-style) bot editing.

Backend coverage for the "fully open" bot model: lightweight creation, setting
named traits + vitals straight into sheet_json with no approval gate, and the
payoff — the roller reads exactly what the bot writes. The discord.py cogs are
offline-tested separately; here we exercise the db helpers + bot-API endpoints.
"""
from core.dice import resolve_pool
from web.db import (_sheet_trait_index, apply_sheet_traits, create_bot_character,
                    get_character, get_db, set_character_vitals)

_BOT = {"Authorization": "Bearer smoke-test-token"}


# ── db helpers ────────────────────────────────────────────────────────────────

def test_create_bot_character_seeds_minimal_sheet(_client):
    with get_db() as conn:
        ch = create_bot_character(conn, discord_id="sl-vamp", name="Lydia", splat="vampire")
        full = get_character(conn, ch["id"])
    assert ch["character_type"] == "kindred"
    assert ch["is_approved"] == 0          # no approval gate — unapproved by design
    sheet = full["sheet_json"]
    assert sheet["humanity"] == 7 and sheet["hunger"] == 1 and sheet["blood_potency"] == 1


def test_create_bot_character_splats(_client):
    with get_db() as conn:
        tb = create_bot_character(conn, discord_id="sl-tb", name="Thinny", splat="thin-blood")
        gh = create_bot_character(conn, discord_id="sl-gh", name="Renfield", splat="ghoul")
        mo = create_bot_character(conn, discord_id="sl-mo", name="Norman", splat="mortal")
        tbf = get_character(conn, tb["id"])
        ghf = get_character(conn, gh["id"])
        mof = get_character(conn, mo["id"])
    assert tbf["clan"] == "thin-blood" and tbf["sheet_json"]["blood_potency"] == 0
    assert ghf["character_type"] == "ghoul" and "hunger" not in ghf["sheet_json"]
    assert mof["character_type"] == "mortal" and "blood_potency" not in mof["sheet_json"]


def test_apply_sheet_traits_maps_clamps_and_drops(_client):
    with get_db() as conn:
        ch = create_bot_character(conn, discord_id="sl-tr", name="Tara", splat="vampire")
        res = apply_sheet_traits(conn, ch["id"],
                                 {"Strength": 3, "Brawl": 2, "Dominate": 1, "Bogus": 4})
        s1 = get_character(conn, ch["id"])["sheet_json"]
        apply_sheet_traits(conn, ch["id"], {"Strength": 9})            # clamps to 5
        s2 = get_character(conn, ch["id"])["sheet_json"]
        apply_sheet_traits(conn, ch["id"], {"Strength": 0})            # 0 drops the key
        s3 = get_character(conn, ch["id"])["sheet_json"]
    assert s1["attr_strength"] == 3
    assert res["unknown"] == ["Bogus"]
    assert len(res["applied"]) == 3        # Strength, Brawl, Dominate mapped
    assert s2["attr_strength"] == 5
    assert "attr_strength" not in s3


def test_roller_reads_bot_set_traits(_client):
    """The whole point: traits set via the bot path resolve in the dice engine."""
    with get_db() as conn:
        ch = create_bot_character(conn, discord_id="sl-roll", name="Vera", splat="vampire")
        apply_sheet_traits(conn, ch["id"], {"Strength": 3, "Brawl": 2})
        sheet = get_character(conn, ch["id"])["sheet_json"]
    pool, _parts, unknown = resolve_pool("strength + brawl", sheet, _sheet_trait_index())
    assert pool == 5 and not unknown


def test_set_character_vitals_clamps_and_keeps_meaningful_zero(_client):
    with get_db() as conn:
        ch = create_bot_character(conn, discord_id="sl-vit", name="Mara", splat="vampire")
        out = set_character_vitals(conn, ch["id"], hunger=3, humanity=6,
                                   blood_potency=2, stains=1)
        s1 = get_character(conn, ch["id"])["sheet_json"]
        # Humanity 0 is a real rating (kept); Hunger 0 means "none" (dropped).
        set_character_vitals(conn, ch["id"], humanity=0, hunger=0)
        s2 = get_character(conn, ch["id"])["sheet_json"]
    assert out["hunger"] == 3
    assert s1["humanity"] == 6 and s1["blood_potency"] == 2 and s1["stains"] == 1
    assert s2.get("humanity") == 0 and "hunger" not in s2


# ── bot API endpoints ─────────────────────────────────────────────────────────

def test_quick_create_endpoint(_client):
    r = _client.post("/api/characters/quick", headers=_BOT,
                     json={"discord_id": "sl-api", "username": "ApiUser",
                           "name": "Quickling", "splat": "vampire"})
    assert r.status_code == 201
    cid = r.json()["id"]
    g = _client.get(f"/api/characters/{cid}", headers=_BOT)
    assert g.json()["sheet_json"]["humanity"] == 7


def test_traits_and_vitals_endpoints(_client):
    cid = _client.post("/api/characters/quick", headers=_BOT,
                       json={"discord_id": "sl-api2", "name": "Editable",
                             "splat": "vampire"}).json()["id"]
    r = _client.post(f"/api/characters/{cid}/traits", headers=_BOT,
                     json={"traits": {"Wits": 3, "Awareness": 2, "Nope": 1}})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"]["attr_wits"] == 3 and body["unknown"] == ["Nope"]
    r = _client.post(f"/api/characters/{cid}/vitals", headers=_BOT,
                     json={"hunger": 2, "blood_potency": 3})
    assert r.status_code == 200 and r.json()["state"]["hunger"] == 2


def test_bot_editing_endpoints_require_token(_client):
    assert _client.post("/api/characters/quick",
                        json={"discord_id": "x", "name": "NoAuth", "splat": "mortal"}).status_code == 401
    assert _client.post("/api/characters/1/traits",
                        json={"traits": {"Strength": 2}}).status_code == 401
    assert _client.post("/api/characters/1/vitals", json={"hunger": 1}).status_code == 401


# ── bot cog: trait-string parser (the one runtime-testable cog helper) ─────────

def test_parse_traits_cog_helper():
    from bot.cogs.characters import _parse_traits
    out, bad = _parse_traits("Strength=3 brawl=2, dominate=1 junk nope=x")
    assert out == {"Strength": 3, "brawl": 2, "dominate": 1}
    assert "junk" in bad and "nope=x" in bad
    assert _parse_traits("") == ({}, [])


def test_parse_vr_syntax_cog_helper():
    """Inconnu /vr 'POOL HUNGER DIFFICULTY' parsing."""
    from bot.cogs.roll import _parse_vr_syntax
    assert _parse_vr_syntax("7 2 1") == ("7", 2, 1, False)
    assert _parse_vr_syntax("strength+brawl 2 3") == ("strength+brawl", 2, 3, False)
    assert _parse_vr_syntax("strength + brawl 2") == ("strength+brawl", 2, 0, False)
    assert _parse_vr_syntax("brawl surge") == ("brawl", None, 0, True)
    assert _parse_vr_syntax("5") == ("5", None, 0, False)
    assert _parse_vr_syntax("") == ("0", None, 0, False)


def test_resolve_pool_specialty_dot_notation():
    """Inconnu-style 'melee.swords' grants +1 when the character owns the spec."""
    idx = _sheet_trait_index()
    sheet = {"attr_strength": 2, "skill_melee": 3,
             "specialties": [{"name": "Swords", "skill": "skill_melee"}]}
    assert resolve_pool("melee.swords", sheet, idx)[0] == 4          # 3 + spec
    pool, _p, unknown = resolve_pool("melee.guns", sheet, idx)
    assert pool == 3 and "guns?" in unknown                          # no spec → flagged
    assert resolve_pool("strength+melee.swords", sheet, idx)[0] == 6  # mixes fine


# ── dice-face emoji (Inconnu-style) ───────────────────────────────────────────

def test_dicemoji_face_key():
    from bot.dicemoji import face_key
    assert face_key(1, False) == "ln_bestial"   # a 1 = bestial
    assert face_key(10, False) == "ln_crit"     # a 10 = crit
    assert face_key(7, False) == "ln_succ"      # 6-9 = success
    assert face_key(3, False) == "ln_fail"      # 2-5 = fail
    assert face_key(1, True) == "h_bestial"     # Hunger dice use the h_ set
    assert face_key(10, True) == "h_crit"


def test_dicemoji_emojify_with_fallback():
    from bot.dicemoji import emojify
    emap = {"ln_crit": "<:c:1>", "ln_fail": "<:f:2>"}
    assert emojify([10, 3], False, emap) == "<:c:1> <:f:2>"
    # a face missing from the map falls back to the bare number
    assert emojify([10, 7], False, emap) == "<:c:1> 7"
    assert emojify([10, 3], False, {}) == "10 3"


def test_render_dice_keeps_numbers_under_emoji():
    from bot.cogs.roll import _render_dice
    emap = {"ln_crit": "<:c:1>", "ln_fail": "<:f:2>"}
    out = _render_dice([10, 3], emoji_map=emap)
    assert out.startswith("<:c:1> <:f:2>")          # emoji row on top
    assert out.split("\n")[1] == "**10** · 3"       # numbers kept on a row below
    assert _render_dice([10, 3], emoji_map={}) == "**10** · 3"   # no emoji → numbers only

