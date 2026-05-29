"""Smoke test for /coterie status embed builder. Offline — no Discord needed."""
import os

os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS",   "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from bot.cogs.coteries import _build_coterie_embed, _dots   # noqa: E402


def test_dots():
    assert _dots(0) == "○○○○○"
    assert _dots(2) == "●●○○○"
    assert _dots(5) == "●●●●●"


def test_coterie_embed_full():
    data = {
        "character_id":   1,
        "character_name": "Valeria Morano",
        "coterie": {
            "id": 1, "name": "Dusk", "chasse": 2, "lien": 1, "portillon": 0,
            "status": "active", "member_count": 2,
        },
        "members": [
            {"character_id": 1, "name": "Valeria Morano", "clan": "brujah",
             "role": "leader", "player": "TestPlayer"},
            {"character_id": 3, "name": "Marcus Volkov",  "clan": "ventrue",
             "role": "member", "player": "TestPlayer"},
        ],
    }
    e = _build_coterie_embed(data)
    assert e.title == "🩸 Dusk"
    assert "Valeria Morano" in e.description
    field_names = [f.name for f in e.fields]
    assert "Domain" in field_names
    assert any(n.startswith("Members") for n in field_names)
    # Domain dots render the right counts
    domain_field = next(f for f in e.fields if f.name == "Domain")
    assert "●●○○○" in domain_field.value   # chasse 2
    assert "●○○○○" in domain_field.value   # lien 1
    # Leader marker
    members_field = next(f for f in e.fields if f.name.startswith("Members"))
    assert "Leader" in members_field.value
    assert "Valeria Morano" in members_field.value
    assert "Marcus Volkov"  in members_field.value


def test_coterie_embed_one_other():
    """Description should grammatically handle 1 other member."""
    data = {
        "character_id":   1, "character_name": "Valeria Morano",
        "coterie": {"id": 1, "name": "Dusk", "chasse": 1, "lien": 0,
                    "portillon": 0, "status": "active", "member_count": 2},
        "members": [
            {"character_id": 1, "name": "Valeria Morano", "clan": "brujah",
             "role": "member", "player": "TestPlayer"},
            {"character_id": 3, "name": "Marcus Volkov", "clan": "ventrue",
             "role": "member", "player": "TestPlayer"},
        ],
    }
    e = _build_coterie_embed(data)
    # 1 other → no 's'
    assert "1 other." in e.description or "1 other_" in e.description


def test_coterie_embed_handles_unknown_clan():
    data = {
        "character_id":   1, "character_name": "X",
        "coterie": {"id": 1, "name": "Lonely", "chasse": 1, "lien": 0,
                    "portillon": 0, "status": "active", "member_count": 1},
        "members": [
            {"character_id": 1, "name": "X", "clan": None,
             "role": "member", "player": None},
        ],
    }
    e = _build_coterie_embed(data)
    members_field = next(f for f in e.fields if f.name.startswith("Members"))
    # No clan field appended
    assert "**X**" in members_field.value
