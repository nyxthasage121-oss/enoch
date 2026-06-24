"""V5 crippling-injury chart — pure logic.

Lifted from tiltowait/inconnu (MIT); the chart text is V5 game data. A crippling
injury can occur when a character is Impaired (Health full) and takes more
Aggravated damage: roll a d10, add the total Aggravated damage, and read the
band. The 9-10 band offers two results — the Storyteller picks which applies.
"""
from __future__ import annotations

import random

# (low, high, [(injury, effect), ...]) — read by (d10 + total Aggravated).
_CHART: list[tuple[int, int, list[tuple[str, str]]]] = [
    (1, 6,  [("Stunned", "Spend 1 Willpower or lose one turn.")]),
    (7, 8,  [("Severe head trauma", "Physical rolls lose 1 die; Mental rolls lose 2.")]),
    (9, 10, [("Broken limb or joint", "Rolls using the affected limb lose 3 dice."),
             ("Blinded", "Vision-related rolls lose 3 dice.")]),
    (11, 11, [("Massive wound", "All rolls lose 2 dice; add 1 to all damage suffered.")]),
    (12, 12, [("Crippled", "Limb is lost or mangled beyond use — lose 3 dice when using it.")]),
    (13, 999, [("Death or torpor", "Mortals die; vampires enter immediate torpor.")]),
]


def crippling_injury(aggravated: int, rng: random.Random | None = None) -> dict:
    """Roll the crippling-injury chart (d10 + total Aggravated). Returns
    ``{die, roll, aggravated, injuries: [{name, effect}]}``. Pass a seeded
    ``random.Random`` for deterministic results."""
    r = rng or random
    die = r.randint(1, 10)
    roll = max(0, int(aggravated)) + die
    items = next((it for lo, hi, it in _CHART if lo <= roll <= hi), _CHART[0][2])
    return {
        "die": die,
        "roll": roll,
        "aggravated": max(0, int(aggravated)),
        "injuries": [{"name": n, "effect": e} for n, e in items],
    }
