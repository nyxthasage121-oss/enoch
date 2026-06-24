"""Offline test for build_posted_roll_embed — the web→Discord roll embed
(migration 054). No Discord connection; asserts embed structure only."""
import os

# bot/config.py reads these at import time and crashes on empty values
os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS",   "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from bot.cogs.roll import build_posted_roll_embed  # noqa: E402
from bot.cogs.outbox import build_vitals_embeds  # noqa: E402


def _payload(**over):
    p = {
        "channel_id": "123", "character_name": "Valeria",
        "roller_discord_id": "111111111111111111",
        "outcome": "success", "outcome_label": "Success", "is_win": True,
        "successes": 3, "difficulty": 2, "margin": 1, "pool": 5, "hunger": 1,
        "normal_dice": [10, 7, 6, 4], "hunger_dice": [8],
        "pool_label": "Strength 3 + Brawl 2 = 5d", "note": None,
    }
    p.update(over)
    return p


def test_posted_embed_basic_structure():
    e = build_posted_roll_embed(_payload())
    assert "Valeria" in e.title
    assert "Success" in e.description
    assert "<@111111111111111111>" in e.description          # roller attribution
    fields = {f.name: f.value for f in e.fields}
    assert {"Dice", "Hunger", "Result"} <= set(fields)
    assert "3 successes" in fields["Result"] and "difficulty 2" in fields["Result"]
    assert "Strength 3 + Brawl 2 = 5d" in (e.footer.text or "")
    assert "web tracker" in (e.footer.text or "")


def test_posted_embed_messy_is_blood_colored():
    e = build_posted_roll_embed(_payload(outcome="messy_critical",
                                         outcome_label="Messy Critical Success"))
    assert e.color.value == 0x8B1A1A
    assert "Messy Critical Success" in e.description


def test_posted_embed_win_is_gold():
    e = build_posted_roll_embed(_payload(outcome="critical",
                                         outcome_label="Critical Success"))
    assert e.color.value == 0xC29B48


def test_posted_embed_no_hunger_field_when_zero():
    e = build_posted_roll_embed(_payload(hunger=0, hunger_dice=[]))
    assert "Hunger" not in {f.name for f in e.fields}


def test_posted_embed_surge_note_field():
    e = build_posted_roll_embed(_payload(note="+2 dice · Rouse 7 → no Hunger gained"))
    assert any(f.name == "Blood Surge" for f in e.fields)


def test_posted_embed_no_roller_mention_when_absent():
    e = build_posted_roll_embed(_payload(roller_discord_id=None))
    assert "<@" not in e.description
    assert "Success" in e.description


def test_posted_embed_tolerates_sparse_payload():
    # A minimal payload must still render (best-effort posting must never crash).
    e = build_posted_roll_embed({"character_name": "X", "outcome": "failure"})
    assert "X" in e.title
    assert any(f.name == "Result" for f in e.fields)


# ── vitals board embed (ST-tracker, migration 055) ────────────────────────────

def _vrow(**over):
    r = {"name": "Valeria", "player": "TestPlayer", "type": "kindred",
         "hunger": 2, "health": "1/8", "wp": "0/5", "humanity": 7, "xp": 12,
         "flags": []}
    r.update(over)
    return r


def test_vitals_embeds_basic():
    embeds = build_vitals_embeds({
        "channel_id": "1", "count": 2, "generated_by": "DevStaff",
        "rows": [_vrow(), _vrow(name="Marcus", flags=["Ravenous"])]})
    assert len(embeds) == 1
    e = embeds[0]
    assert "Chronicle Vitals" in e.title
    assert "Valeria" in e.description and "Marcus" in e.description
    assert "Ravenous" in e.description
    assert "DevStaff" in (e.footer.text or "")
    assert e.author.name and "2 active" in e.author.name


def test_vitals_embeds_empty():
    embeds = build_vitals_embeds({"channel_id": "1", "count": 0, "rows": []})
    assert len(embeds) == 1
    assert "No active characters" in embeds[0].description


def test_vitals_embeds_chunks_and_caps():
    rows = [_vrow(name=f"C{i}") for i in range(250)]   # > 10 pages of 20
    embeds = build_vitals_embeds({"channel_id": "1", "count": 250, "rows": rows})
    assert len(embeds) == 10                               # capped at 10 embeds/message
    assert "not shown" in (embeds[-1].footer.text or "")   # truncation noted
