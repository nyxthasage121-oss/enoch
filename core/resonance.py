"""V5 blood Resonance + Temperament generator (Corebook pp. 224-229).

Pure logic, shared by the web roller and (later) the bot's `/resonance`.

Each Resonance is tied to one of the four humors and eases two Disciplines.
Temperament is the intensity, weighted to the corebook's occurrence rates
(Negligible most common, Acute rarest). All data here is V5 RAW.
"""
from __future__ import annotations

import random

# Resonance key → display data. Disciplines + emotions are V5 RAW.
RESONANCES: dict[str, dict] = {
    "choleric": {
        "label": "Choleric",
        "disciplines": ["Celerity", "Potence"],
        "emotions": "Angry, violent, bullying, passionate, envious",
    },
    "melancholic": {
        "label": "Melancholic",
        "disciplines": ["Fortitude", "Obfuscate"],
        "emotions": "Sad, scared, depressed, intellectual, grounded",
    },
    "phlegmatic": {
        "label": "Phlegmatic",
        "disciplines": ["Auspex", "Dominate"],
        "emotions": "Lazy, apathetic, calm, controlling, sentimental",
    },
    "sanguine": {
        "label": "Sanguine",
        "disciplines": ["Blood Sorcery", "Presence"],
        "emotions": "Horny, happy, enthusiastic, addicted, active, flighty",
    },
}

# Temperament: (key, label, occurrence weight %, effect). Weights are the V5
# occurrence rates (50 / 30 / 16 / 4); effects are per the corebook.
TEMPERAMENTS: list[tuple[str, str, int, str]] = [
    ("negligible", "Negligible", 50,
     "No mechanical effect — a faint emotional tinge only."),
    ("fleeting", "Fleeting", 30,
     "No dice bonus, but justifies buying dots in the matching Disciplines "
     "(and powers Thin-Blood Alchemy)."),
    ("intense", "Intense", 16,
     "+1 die to the matching Disciplines' pools until your next feeding or Hunger 5."),
    ("acute", "Acute", 4,
     "+1 die (as Intense), and the Blood can carry a Dyscrasia — drain the vessel "
     "or feed from them across three nights, then work the effect out with your ST."),
]
_T_KEYS = [t[0] for t in TEMPERAMENTS]
_T_WEIGHTS = [t[2] for t in TEMPERAMENTS]
_T_BY_KEY = {t[0]: t for t in TEMPERAMENTS}


def roll_resonance(rng: random.Random | None = None) -> dict:
    """Generate a random Resonance + Temperament.

    Returns a flat dict ready for display: the resonance key/label/disciplines/
    emotions, the temperament key/label/effect, plus ``has_bonus`` (Intense or
    Acute) and ``is_acute`` convenience flags. Pass a seeded ``random.Random``
    for deterministic results.
    """
    r = rng or random
    res_key = r.choice(list(RESONANCES))
    res = RESONANCES[res_key]
    t_key = r.choices(_T_KEYS, weights=_T_WEIGHTS, k=1)[0]
    _, t_label, _, t_effect = _T_BY_KEY[t_key]
    return {
        "resonance": res_key,
        "label": res["label"],
        "disciplines": res["disciplines"],
        "emotions": res["emotions"],
        "temperament": t_key,
        "temperament_label": t_label,
        "effect": t_effect,
        "has_bonus": t_key in ("intense", "acute"),
        "is_acute": t_key == "acute",
    }
