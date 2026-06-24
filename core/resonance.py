"""V5 blood Resonance + Temperament generator (Corebook pp. 224-229).

Pure logic, shared by the web roller and (later) the bot's `/resonance`.

Each Resonance is tied to one of the four humors and eases two Disciplines.
Temperament is the intensity, weighted to the corebook's occurrence rates
(Negligible most common, Acute rarest). The chronicle picks a Resonance MODE:

  standard        — V5 core Disciplines.
  tattered_facade — alternate Discipline associations from *Tattered Facade*.
  add_empty       — V5 core, plus a ~1-in-6 chance of an Empty resonance.

The mode switch + dyscrasia-on-Acute behaviour follow tiltowait/inconnu (MIT);
the dyscrasia text is V5 game data (see core/dyscrasias.py).
"""
from __future__ import annotations

import random

from core.dyscrasias import DYSCRASIAS

# Chronicle-wide resonance modes (mirrors db.RESONANCE_MODES).
RESONANCE_MODES = ("standard", "tattered_facade", "add_empty")

# Resonance key → display label + emotions (V5 RAW).
RESONANCES: dict[str, dict] = {
    "phlegmatic":  {"label": "Phlegmatic",  "emotions": "Lazy, apathetic, calm, controlling, sentimental"},
    "melancholic": {"label": "Melancholic", "emotions": "Sad, scared, depressed, intellectual, grounded"},
    "choleric":    {"label": "Choleric",    "emotions": "Angry, violent, bullying, passionate, envious"},
    "sanguine":    {"label": "Sanguine",    "emotions": "Horny, happy, enthusiastic, addicted, active, flighty"},
    "empty":       {"label": "Empty",       "emotions": "No notable emotion"},
}

# Disciplines eased by each Resonance — standard V5 vs the Tattered Facade chart.
STANDARD_DISCIPLINES = {
    "choleric":    ["Celerity", "Potence"],
    "melancholic": ["Fortitude", "Obfuscate"],
    "phlegmatic":  ["Auspex", "Dominate"],
    "sanguine":    ["Blood Sorcery", "Presence"],
    "empty":       ["Oblivion"],
}
TATTERED_DISCIPLINES = {
    "choleric":    ["Animalism", "Celerity", "Potence"],
    "melancholic": ["Fortitude", "Obfuscate", "Oblivion"],
    "phlegmatic":  ["Auspex", "Dominate"],
    "sanguine":    ["Blood Sorcery", "Presence", "Protean"],
    "empty":       ["Oblivion"],
}

# Temperament: (key, label, occurrence weight %, effect). 50/30/16/4 = V5 RAW.
TEMPERAMENTS: list[tuple[str, str, int, str]] = [
    ("negligible", "Negligible", 50,
     "No mechanical effect — the Blood carries no usable Resonance this feed."),
    ("fleeting", "Fleeting", 30,
     "No dice bonus, but justifies buying dots in the matching Disciplines "
     "(and powers Thin-Blood Alchemy)."),
    ("intense", "Intense", 16,
     "+1 die to the matching Disciplines' pools until your next feeding or Hunger 5."),
    ("acute", "Acute", 4,
     "+1 die (as Intense), and the Blood carries a Dyscrasia — drain the vessel "
     "or feed from them across three nights."),
]
_T_KEYS = [t[0] for t in TEMPERAMENTS]
_T_WEIGHTS = [t[2] for t in TEMPERAMENTS]
_T_BY_KEY = {t[0]: t for t in TEMPERAMENTS}


def _disciplines_for(mode: str) -> dict:
    return TATTERED_DISCIPLINES if mode == "tattered_facade" else STANDARD_DISCIPLINES


def _roll_resonance_type(mode: str, rng: random.Random) -> str:
    """V5 core weighted table: Phlegmatic / Melancholic 30% each, Choleric /
    Sanguine 20% each. In ``add_empty`` mode a d12 adds a ~16.7% Empty chance."""
    cap = 12 if mode == "add_empty" else 10
    die = rng.randint(1, cap)
    if die <= 3:
        return "phlegmatic"
    if die <= 6:
        return "melancholic"
    if die <= 8:
        return "choleric"
    if die <= 10:
        return "sanguine"
    return "empty"


def get_dyscrasia(resonance: str, rng: random.Random | None = None) -> dict | None:
    """A random Dyscrasia ({name, description, page}) for a resonance, or None
    for Empty / unknown resonances."""
    pool = DYSCRASIAS.get(resonance) or []
    if not pool:
        return None
    return (rng or random).choice(pool)


def roll_resonance(mode: str = "standard",
                   rng: random.Random | None = None) -> dict:
    """Generate a Resonance + Temperament under the given chronicle ``mode``.

    Returns a flat dict for display. A Negligible temperament yields no usable
    Resonance (``resonance`` is None). An Acute, non-Empty result carries a
    Dyscrasia. Pass a seeded ``random.Random`` for deterministic results.
    """
    if mode not in RESONANCE_MODES:
        mode = "standard"
    r = rng or random
    t_key = r.choices(_T_KEYS, weights=_T_WEIGHTS, k=1)[0]
    _, t_label, _, t_effect = _T_BY_KEY[t_key]

    if t_key == "negligible":
        return {
            "mode": mode, "resonance": None, "label": None,
            "disciplines": [], "emotions": None,
            "temperament": t_key, "temperament_label": t_label, "effect": t_effect,
            "has_bonus": False, "is_acute": False, "dyscrasia": None,
        }

    res_key = _roll_resonance_type(mode, r)
    res = RESONANCES[res_key]
    disciplines = _disciplines_for(mode).get(res_key, [])
    dyscrasia = (get_dyscrasia(res_key, r)
                 if (t_key == "acute" and res_key != "empty") else None)
    return {
        "mode": mode,
        "resonance": res_key,
        "label": res["label"],
        "disciplines": disciplines,
        "emotions": res["emotions"],
        "temperament": t_key,
        "temperament_label": t_label,
        "effect": t_effect,
        "has_bonus": t_key in ("intense", "acute"),
        "is_acute": t_key == "acute",
        "dyscrasia": dyscrasia,
    }
