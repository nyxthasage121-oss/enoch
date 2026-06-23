"""xp_rules.py — XP cost calculation and spend validation.

Loads packages/rules/xp_costs.json once at import time.
All functions are pure (no DB calls) and safe to call from route handlers.
"""
import json
from pathlib import Path

_RULES_PATH = Path(__file__).parent.parent / "packages" / "rules" / "xp_costs.json"
RULES: dict = json.loads(_RULES_PATH.read_text(encoding="utf-8"))

INGRAINED_XP_CAP: int = RULES.get("Ingrained Discipline", {}).get("flaw_xp_cap", 15)

# Canonical ordered category list for form dropdowns
SPEND_CATEGORIES: list[str] = [
    "Attribute",
    "Skill",
    "Specialty",
    "Clan Discipline",
    "Other Discipline",
    "Caitiff Discipline",
    "Ghoul Discipline",
    "Ingrained Discipline",
    "Blood Sorcery Ritual",
    "Oblivion Ceremony",
    "Thin-Blood Alchemy Formula",
    "Advantage",
    # Loresheets count as Backgrounds in this chronicle — purchased
    # under Advantage in the spend form rather than as their own line.
    "Blood Potency",
    "Humanity",
]

HUMANITY_CONDITIONS: list[str] = RULES.get("Humanity", {}).get("conditions", [])


# ── Cost calculation ──────────────────────────────────────────────────────────

def calculate_cost(
    category: str,
    current_dots: int,
    new_dots: int,
) -> tuple[int, str | None]:
    """
    Calculate the XP cost for a trait purchase.

    Returns (cost, error_message).
    If error_message is not None, cost is 0 and the purchase is invalid.

    Cost rules:
      multiplier    — progressive: sum of (each new dot × multiplier)
                      e.g. Attribute 1→3 = (2×5) + (3×5) = 25 XP
      flat_cost     — fixed amount, only valid for 0→1 transitions
      flat_per_dot  — (new - current) × cost_per_dot
      level_multiplier — new_dots × multiplier (rituals/formulas, no progressive summing)
    """
    rule = RULES.get(category)
    if rule is None:
        return 0, f"Unknown category: {category!r}"

    if new_dots <= current_dots:
        return 0, "New dots must be greater than current dots"

    min_d = rule.get("min_dots", 0)
    max_d = rule.get("max_dots", 5)

    if current_dots < min_d:
        return 0, (
            f"{category} requires current dots ≥ {min_d} "
            f"(you have {current_dots})"
        )
    if new_dots > max_d:
        return 0, f"{category} maximum is {max_d} dots (requested {new_dots})"

    # Flat cost — only 0→1
    if "flat_cost" in rule:
        if current_dots != 0 or new_dots != 1:
            return 0, f"{category} uses a flat cost and is only purchasable as 0→1"
        return rule["flat_cost"], None

    # Flat per dot
    if "flat_per_dot" in rule:
        return (new_dots - current_dots) * rule["flat_per_dot"], None

    # Level multiplier (rituals / thin-blood formulas)
    if "level_multiplier" in rule:
        return new_dots * rule["level_multiplier"], None

    # Progressive multiplier: each new dot costs dot_number × multiplier
    if "multiplier" in rule:
        m    = rule["multiplier"]
        cost = sum(d * m for d in range(current_dots + 1, new_dots + 1))
        return cost, None

    return 0, f"No cost rule configured for {category!r}"


def validate_spend(
    category: str,
    current_dots: int,
    new_dots: int,
    character: dict,
) -> tuple[int, list[str]]:
    """
    Full validation for a new spend request from a player.

    Returns (verified_cost, errors).
    If errors is non-empty, the spend should be rejected before DB insertion.

    Checks:
      - Valid category
      - Dot range validity
      - Character has sufficient available XP
      - Ingrained Discipline flaw budget (is_ingrained is inferred from category)
      - Humanity: delegates condition checking to validate_humanity_conditions
    """
    errors: list[str] = []
    is_ingrained = (category == "Ingrained Discipline")

    cost, calc_error = calculate_cost(category, current_dots, new_dots)
    if calc_error:
        errors.append(calc_error)
        return 0, errors

    # XP availability
    available = character.get("xp_available", 0)
    if cost > available:
        errors.append(
            f"Insufficient XP — {cost} required, {available} available"
        )

    # Ingrained Discipline budget
    if is_ingrained:
        if not character.get("has_ingrained_flaw"):
            errors.append(
                "Your character does not have the Ingrained Discipline Flaw"
            )
        else:
            used = character.get("ingrained_xp_used", 0)
            if used + cost > INGRAINED_XP_CAP:
                remaining = INGRAINED_XP_CAP - used
                errors.append(
                    f"Ingrained Discipline budget: {remaining} XP remaining "
                    f"of {INGRAINED_XP_CAP} (this purchase costs {cost})"
                )

    # Humanity: only 1 dot at a time
    if category == "Humanity":
        rule = RULES.get("Humanity", {})
        max_inc = rule.get("max_increase", 1)
        if (new_dots - current_dots) > max_inc:
            errors.append(
                f"Humanity can only be purchased {max_inc} dot at a time"
            )

    return cost, errors


def revalidate_spend(spend: dict) -> dict:
    """Recompute the cost of an existing spend request and compare it
    against the stored verified_cost. Returns a dict the staff review
    template can render as a "player said X / system says Y" diff badge.

    Lifted from MCbN's `validate_spend_request` pattern — the point is to
    surface drift between submission-time and review-time, which can
    happen if rule formulas change, the character's clan/discipline
    state shifts, or a manual fix-up was needed.

    Returns:
        {
          "valid":        bool,   # did the rule lookup succeed
          "correct_cost": int,    # what the rules say the cost should be
          "stored_cost":  int,    # spend.verified_cost (what landed in DB)
          "matches":      bool,   # correct_cost == stored_cost
          "message":      str,    # short human-readable note
        }"""
    cat   = spend.get("category") or ""
    cur   = int(spend.get("current_dots") or 0)
    new   = int(spend.get("new_dots")     or 0)
    stored = int(spend.get("verified_cost") or 0)
    correct, calc_error = calculate_cost(cat, cur, new)
    if calc_error:
        return {
            "valid":        False,
            "correct_cost": correct,
            "stored_cost":  stored,
            "matches":      False,
            "message":      f"Rule lookup failed: {calc_error}",
        }
    matches = (correct == stored)
    if matches:
        msg = f"Cost agrees with stored value ({stored} XP)."
    else:
        msg = (
            f"System now says {correct} XP; player submitted {stored} XP "
            f"(Δ {correct - stored:+d})."
        )
    return {
        "valid":        True,
        "correct_cost": correct,
        "stored_cost":  stored,
        "matches":      matches,
        "message":      msg,
    }


def validate_humanity_conditions(conditions_checked: list[bool]) -> tuple[bool, str | None]:
    """
    Verify all four Humanity spend conditions are confirmed.

    conditions_checked must be a list of booleans matching HUMANITY_CONDITIONS.
    Returns (valid, error_message).
    """
    if len(conditions_checked) < len(HUMANITY_CONDITIONS):
        return False, "All Humanity conditions must be acknowledged"
    if not all(conditions_checked):
        unchecked = [
            HUMANITY_CONDITIONS[i]
            for i, v in enumerate(conditions_checked)
            if not v and i < len(HUMANITY_CONDITIONS)
        ]
        return False, "Unmet conditions: " + "; ".join(unchecked)
    return True, None
