"""V5 mechanical-state reminders derived from a character sheet.

Pure logic, shared by the web roller and the Sheet view. Surfaces the
*consequences* of the tracked vitals (Health, Willpower, Hunger, Humanity) so
players and STs don't have to remember them mid-scene.

Deliberately conservative: only the unambiguous corebook states, phrased as
reminders rather than enforced penalties (the chronicle still adjudicates).
"""
from __future__ import annotations

SEV_CRIT = "crit"   # red — a hard mechanical wall (Impaired, Ravenous, lost)
SEV_WARN = "warn"   # amber — a heads-up, one step from trouble


def _i(sheet: dict, key: str) -> int:
    try:
        return int(sheet.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def character_conditions(sheet: dict) -> list[dict]:
    """Active mechanical-state reminders for a sheet.

    Returns a list of ``{key, sev, label, detail}`` dicts (empty when nothing
    notable applies). ``key`` is stable for de-duplication/styling; ``sev`` is
    ``SEV_CRIT`` or ``SEV_WARN``.
    """
    if not isinstance(sheet, dict):
        return []
    out: list[dict] = []

    # ── Health — tracker = Stamina + 3 ───────────────────────────────────────
    hp_max = _i(sheet, "attr_stamina") + 3
    hp_agg = _i(sheet, "damage_health_agg")
    hp_marked = _i(sheet, "damage_health_sup") + hp_agg
    if hp_max > 0 and hp_agg >= hp_max:
        out.append({
            "key": "health", "sev": SEV_CRIT,
            "label": "Health full (Aggravated)",
            "detail": "All Health is Aggravated — torpor (vampire) or Final Death.",
        })
    elif hp_max > 0 and hp_marked >= hp_max:
        out.append({
            "key": "health", "sev": SEV_CRIT,
            "label": "Impaired (Health)",
            "detail": "Health tracker full — Impaired (−2 dice); more damage now "
                      "upgrades Superficial to Aggravated.",
        })

    # ── Willpower — tracker = Composure + Resolve ────────────────────────────
    wp_max = _i(sheet, "attr_composure") + _i(sheet, "attr_resolve")
    wp_marked = _i(sheet, "damage_willpower_sup") + _i(sheet, "damage_willpower_agg")
    if wp_max > 0 and wp_marked >= wp_max:
        out.append({
            "key": "willpower", "sev": SEV_WARN,
            "label": "Impaired (Willpower)",
            "detail": "Willpower tracker full — none left to reroll or boost; "
                      "Impaired (−2 dice) on Willpower-driven pools.",
        })

    # ── Hunger (0-5) ─────────────────────────────────────────────────────────
    hunger = _i(sheet, "hunger")
    if hunger >= 5:
        out.append({
            "key": "hunger", "sev": SEV_CRIT,
            "label": "Ravenous — Hunger 5",
            "detail": "Feeding is urgent — you can't Blush of Life, and Rousing "
                      "now risks a Hunger frenzy. Messy Crits / Bestial Failures peak.",
        })
    elif hunger == 4:
        out.append({
            "key": "hunger", "sev": SEV_WARN,
            "label": "Hunger 4",
            "detail": "One step from Ravenous — Messy Critical and Hunger-frenzy "
                      "risk is high.",
        })

    # ── Humanity (0-10; default 7 when unset) ────────────────────────────────
    humanity = 7 if sheet.get("humanity") is None else _i(sheet, "humanity")
    if humanity <= 0:
        out.append({
            "key": "humanity", "sev": SEV_CRIT,
            "label": "Humanity 0",
            "detail": "Lost to the Beast — the character becomes a wight (NPC).",
        })
    elif humanity <= 2:
        out.append({
            "key": "humanity", "sev": SEV_WARN,
            "label": f"Low Humanity ({humanity})",
            "detail": "Degeneration risk — Stains bite harder and the Beast is close.",
        })

    return out
