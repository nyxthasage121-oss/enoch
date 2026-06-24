"""V5 crippling-injury chart (core/injury.py)."""
import random

from core.injury import crippling_injury


def test_roll_math_and_shape():
    r = crippling_injury(3, random.Random(0))
    assert r["aggravated"] == 3
    assert r["roll"] == r["aggravated"] + r["die"]
    assert r["injuries"] and r["injuries"][0]["name"] and r["injuries"][0]["effect"]


def test_high_total_is_death_or_torpor():
    r = crippling_injury(20, random.Random(1))
    assert r["injuries"][0]["name"] == "Death or torpor"


def test_band_9_10_offers_two_choices():
    two = None
    for seed in range(60):
        rr = crippling_injury(8, random.Random(seed))   # 8 + d10 → 9..18
        if rr["roll"] in (9, 10):
            two = rr
            break
    assert two is not None and len(two["injuries"]) == 2
