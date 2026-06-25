"""Offline tests for the /cripple, /probability, /resonance embed builders —
they wrap the shared core/ engines, so we assert structure, not Discord I/O."""
import os
import random

os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("STAFF_ROLE_IDS", "")
os.environ.setdefault("BOT_SERVICE_TOKEN", "test-token")

from core.dice import probability                  # noqa: E402
from core.injury import crippling_injury           # noqa: E402
from core.resonance import roll_resonance          # noqa: E402
from bot.cogs.reference import (                    # noqa: E402
    build_cripple_embed, build_probability_embed, build_resonance_embed,
)


def test_cripple_embed_lists_injuries():
    e = build_cripple_embed(crippling_injury(3, random.Random(1)))
    assert "Crippling Injury" in e.title
    assert e.fields and all(f.name and f.value for f in e.fields)


def test_cripple_high_aggravated_is_severe():
    # d10 + 12 aggravated always lands in the worst band.
    e = build_cripple_embed(crippling_injury(12, random.Random(0)))
    assert any("torpor" in f.value.lower() or "Death" in f.name for f in e.fields)


def test_probability_embed_shows_percentages():
    e = build_probability_embed(probability(6, 1, 2, trials=500, rng=random.Random(1)))
    assert "Odds" in e.title and "%" in e.description
    names = {f.name for f in e.fields}
    assert {"Pool", "Critical", "Messy", "Bestial fail"} <= names


def test_resonance_embed_non_negligible():
    for seed in range(60):
        rr = roll_resonance("standard", random.Random(seed))
        if rr["resonance"]:
            e = build_resonance_embed(rr)
            assert rr["label"] in e.description
            assert any(f.name == "Disciplines" for f in e.fields)
            return
    raise AssertionError("no usable resonance across 60 seeds")


def test_resonance_embed_negligible():
    for seed in range(60):
        rr = roll_resonance("standard", random.Random(seed))
        if not rr["resonance"]:
            e = build_resonance_embed(rr)
            assert "no usable Resonance" in e.description
            return
    raise AssertionError("no negligible result across 60 seeds")
