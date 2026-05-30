"""Offline tests for the /timeskip embed builder (bot/cogs/timeskip.py)."""
import os

os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS", "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from bot.cogs.timeskip import (  # noqa: E402
    build_timeskip_embed, _epoch, _kind, _ts,
)


def test_epoch_parses_iso_with_and_without_zone():
    # A known instant: 2026-05-30T00:00:00Z = 1780099200.
    assert _epoch("2026-05-30T00:00:00Z") == 1780099200
    assert _epoch("2026-05-30T00:00:00+00:00") == 1780099200
    # Naïve timestamps are assumed UTC.
    assert _epoch("2026-05-30T00:00:00") == 1780099200
    assert _epoch(None) is None
    assert _epoch("not-a-date") is None


def test_ts_emits_discord_timestamp_tag():
    assert _ts("2026-05-30T00:00:00Z") == "<t:1780099200:F>"
    assert _ts("2026-05-30T00:00:00Z", "R") == "<t:1780099200:R>"
    assert _ts(None) == "—"


def test_kind_skips_empty_and_full_phase():
    assert _kind({"period_type": "night", "phase": "full"}) == "Night"
    assert _kind({"period_type": "down_time", "phase": "dusk"}) == "Down Time · Dusk"
    assert _kind({"period_type": "", "phase": ""}) == ""


def test_embed_active_period_shows_window_and_label():
    data = {
        "active": True,
        "period": {
            "label": "Night 42 — Dusk to Midnight",
            "period_type": "night", "phase": "full",
            "opens_at": "2026-05-29T20:00:00Z",
            "closes_at": "2026-05-31T04:00:00Z",
        },
        "upcoming": [
            {"label": "Night 43", "period_type": "night",
             "opens_at": "2026-06-12T20:00:00Z"},
        ],
    }
    e = build_timeskip_embed(data)
    assert "Current Timeskip" in e.title
    assert "Night 42" in e.description
    win = next(f for f in e.fields if f.name == "Window").value
    assert "Opened <t:" in win and "Closes <t:" in win
    deck = next(f for f in e.fields if f.name == "On Deck").value
    assert "Night 43" in deck and "opens <t:" in deck


def test_embed_no_active_period_shows_placeholder_and_upcoming():
    data = {
        "active": False, "period": None,
        "upcoming": [{"label": "Night 50",
                      "opens_at": "2026-07-01T20:00:00Z"}],
    }
    e = build_timeskip_embed(data)
    assert "No timeskip is open" in e.description
    deck = next(f for f in e.fields if f.name == "On Deck").value
    assert "Night 50" in deck


def test_embed_no_active_no_upcoming_says_nothing_scheduled():
    e = build_timeskip_embed({"active": False, "period": None, "upcoming": []})
    deck = next(f for f in e.fields if f.name == "On Deck").value
    assert "Nothing scheduled" in deck
