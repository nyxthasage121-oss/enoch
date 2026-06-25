"""settings_enums.py — single source of truth for the admin settings dropdowns.

Each enum lists its options as ``{value, label, desc[, disabled]}`` so the admin
template renders them AND the settings route/db validate against the SAME list:
add or rename an option in one place and both follow. Value-only tuples/sets are
derived for membership checks, and ``db.py`` re-exports them under their historical
names (``RULESETS``, ``RESONANCE_MODES``, ``PROJECT_MODES``) so existing importers
keep working.

This module imports nothing from the app (no circular import with db.py).
"""

# Character-creation mode — Guided (enforced wizard) vs Open (free entry).
CREATION_MODE_OPTIONS = [
    {"value": "guided", "label": "Guided creation",
     "desc": "Wizard enforces RAW / In Memoriam / Homebrew"},
    {"value": "open", "label": "Open entry",
     "desc": "Players just enter their sheet — nothing enforced"},
]

# Base budget ruleset. In Memoriam is an orthogonal flag, NOT a value here.
RULESET_OPTIONS = [
    {"value": "standard", "label": "Standard", "desc": "V5 RAW defaults"},
    {"value": "homebrew", "label": "Homebrew", "desc": "Chronicle-tuned budgets"},
]

# Which Resonance chart the Roll tab's generator uses (migration 052).
RESONANCE_MODE_OPTIONS = [
    {"value": "standard", "label": "Standard — V5 core",
     "desc": "The V5 core Resonance chart."},
    {"value": "tattered_facade", "label": "Tattered Facade — alternate Disciplines",
     "desc": "Alternate Discipline links (Choleric +Animalism, Melancholy +Oblivion, Sanguine +Protean)."},
    {"value": "add_empty", "label": "Add Empty — +1-in-6 Empty resonance",
     "desc": "Adds a ~1-in-6 chance of an Empty (no usable) resonance."},
]

# Chronicle-wide downtime-project engine (migration 043). 'raw' is not finished.
PROJECT_MODE_OPTIONS = [
    {"value": "nybn", "label": "NYbN — multi-stage extended test",
     "desc": "The NYbN multi-stage extended-test engine."},
    {"value": "homebrew", "label": "Homebrew — staff-set goal, optional launch",
     "desc": "Staff set the goal; an optional launch roll opens it."},
    {"value": "off", "label": "Off — Projects disabled",
     "desc": "Hides Projects everywhere (player tab, coterie panel, staff queue)."},
    {"value": "raw", "label": "RAW — coming soon",
     "desc": "RAW project rules — still in progress.", "disabled": True},
]


def _values(options) -> tuple:
    return tuple(o["value"] for o in options)


# Derived value collections (the historical shapes: tuples, except PROJECT_MODES
# which has always been a set for membership checks).
CREATION_MODES = _values(CREATION_MODE_OPTIONS)          # ("guided", "open")
RULESETS = _values(RULESET_OPTIONS)                      # ("standard", "homebrew")
RESONANCE_MODES = _values(RESONANCE_MODE_OPTIONS)
PROJECT_MODES = set(_values(PROJECT_MODE_OPTIONS))       # {"nybn","homebrew","raw","off"}
