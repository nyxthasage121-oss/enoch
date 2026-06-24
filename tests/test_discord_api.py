"""discord_api.py — the read-only guild channel/role parsers + helpers that back
the admin UI's bot-powered pickers. Pure-logic tests; no network."""
from web.discord_api import _parse_channels, _parse_roles


def test_parse_channels_filters_textlike_and_sorts():
    data = [
        {"id": 1, "type": 4, "name": "TEXT CHANNELS"},                       # category
        {"id": 2, "type": 0, "name": "general",  "position": 1, "parent_id": 1},
        {"id": 3, "type": 2, "name": "Voice",    "position": 0},             # voice → excluded
        {"id": 4, "type": 5, "name": "announce", "position": 0, "parent_id": 1},
        {"id": 5, "type": 0, "name": "dice",     "position": 2, "parent_id": 1},
    ]
    chans = _parse_channels(data)
    assert [c["name"] for c in chans] == ["announce", "general", "dice"]
    assert all(isinstance(c["id"], str) for c in chans)            # snowflakes as strings
    assert chans[0]["category"] == "TEXT CHANNELS"


def test_parse_channels_empty():
    assert _parse_channels(None) == []
    assert _parse_channels([]) == []


def test_parse_roles_excludes_everyone_and_managed():
    data = [
        {"id": "100", "name": "@everyone", "position": 0},                   # == guild id
        {"id": "200", "name": "Storyteller", "position": 5},
        {"id": "300", "name": "BotRole", "position": 3, "managed": True},    # managed → excluded
        {"id": "400", "name": "Player", "position": 1},
    ]
    roles = _parse_roles(data, guild_id=100)
    assert [r["name"] for r in roles] == ["Storyteller", "Player"]           # highest first


def test_parse_roles_empty():
    assert _parse_roles(None, 1) == []


def test_guild_helpers_empty_without_token():
    # conftest sets DISCORD_BOT_TOKEN="" so the helpers short-circuit to [] and
    # never touch the network.
    import asyncio
    from web.discord_api import guild_roles, guild_text_channels
    assert asyncio.run(guild_text_channels()) == []
    assert asyncio.run(guild_roles()) == []
