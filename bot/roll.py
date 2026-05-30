"""V5 dice roller — pure logic, no Discord dependency (offline-testable).

Implements the Vampire: The Masquerade 5th Edition roll. The mechanics here
are the public V5 corebook rules (Chapter 4, "Dice Rolls", pp. 118-120); the
implementation is original — modeled on the familiar Inconnu command UX but
not derived from its source.

The roll:
  - Form a pool of d10s. ``hunger`` of those dice are Hunger dice (capped at
    5 and at the pool size).
  - A die showing 6-10 is a success.
  - Each PAIR of 10s counts as four successes instead of two (i.e. +2 bonus
    successes per pair) — a *critical*.
  - *Messy Critical*: a winning critical where at least one of the paired 10s
    landed on a Hunger die.
  - *Bestial Failure*: a roll that does NOT meet the difficulty and shows at
    least one 1 on a Hunger die.
  - Win when total successes >= difficulty (or, with no difficulty set, when
    there is at least one success).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# Outcome constants — the authoritative result of a roll.
CRITICAL       = "critical"        # winning roll with a pair of 10s
MESSY_CRITICAL = "messy_critical"  # ...where a 10 was on a Hunger die
SUCCESS        = "success"
FAILURE        = "failure"         # missed difficulty, but had some successes
TOTAL_FAILURE  = "total_failure"   # zero successes
BESTIAL_FAILURE = "bestial_failure"  # failed roll with a 1 on a Hunger die

# Human-facing labels for the outcomes.
OUTCOME_LABELS = {
    CRITICAL:        "Critical Win",
    MESSY_CRITICAL:  "Messy Critical",
    SUCCESS:         "Success",
    FAILURE:         "Failure",
    TOTAL_FAILURE:   "Total Failure",
    BESTIAL_FAILURE: "Bestial Failure",
}

_MAX_POOL = 50   # sanity cap so a typo can't roll thousands of dice
_MAX_HUNGER = 5


@dataclass
class RollResult:
    """The outcome of a V5 roll. ``normal_dice``/``hunger_dice`` are the rolled
    faces (descending) so a caller can render them."""
    pool: int
    hunger: int
    difficulty: int
    normal_dice: list[int]
    hunger_dice: list[int]
    successes: int
    outcome: str
    critical: bool   # a pair of 10s exists (regardless of win/loss)
    messy: bool      # a 10 landed on a Hunger die within a critical pair
    bestial: bool    # a 1 landed on a Hunger die on a failed roll

    @property
    def margin(self) -> int:
        """Successes beyond (or short of) the difficulty."""
        return self.successes - self.difficulty

    @property
    def is_win(self) -> bool:
        return self.outcome in (CRITICAL, MESSY_CRITICAL, SUCCESS)


def classify(normal_dice: list[int], hunger_dice: list[int],
             difficulty: int = 0) -> RollResult:
    """Score a set of already-rolled dice. Separated from rolling so the V5
    rules can be unit-tested with hand-picked dice (no randomness)."""
    difficulty = max(0, int(difficulty))
    all_dice = list(normal_dice) + list(hunger_dice)

    tens = sum(1 for d in all_dice if d == 10)
    base = sum(1 for d in all_dice if d >= 6)
    crit_pairs = tens // 2
    successes = base + crit_pairs * 2

    critical = crit_pairs >= 1
    hunger_tens = sum(1 for d in hunger_dice if d == 10)
    # A critical is "messy" when a Hunger 10 is part of a scoring pair. With
    # `crit_pairs` pairs available, any Hunger 10 (up to that count) makes it
    # messy.
    messy = critical and hunger_tens >= 1

    win = (successes >= difficulty) if difficulty > 0 else (successes > 0)
    bestial = (not win) and any(d == 1 for d in hunger_dice)

    if win and critical:
        outcome = MESSY_CRITICAL if messy else CRITICAL
    elif win:
        outcome = SUCCESS
    elif bestial:
        outcome = BESTIAL_FAILURE
    elif successes == 0:
        outcome = TOTAL_FAILURE
    else:
        outcome = FAILURE

    return RollResult(
        pool=len(all_dice),
        hunger=len(hunger_dice),
        difficulty=difficulty,
        normal_dice=sorted(normal_dice, reverse=True),
        hunger_dice=sorted(hunger_dice, reverse=True),
        successes=successes,
        outcome=outcome,
        critical=critical,
        messy=messy,
        bestial=bestial,
    )


def roll_pool(pool: int, hunger: int = 0, difficulty: int = 0,
              rng: random.Random | None = None) -> RollResult:
    """Roll a V5 pool. ``hunger`` dice (capped at 5 and at the pool) are Hunger
    dice. Pass a seeded ``random.Random`` for deterministic results."""
    r = rng or random
    pool = max(0, min(int(pool), _MAX_POOL))
    hunger = max(0, min(int(hunger), _MAX_HUNGER, pool))
    normal_n = pool - hunger
    normal = [r.randint(1, 10) for _ in range(normal_n)]
    hung = [r.randint(1, 10) for _ in range(hunger)]
    return classify(normal, hung, difficulty)


# ── Trait-pool resolution (connects a roll to a character sheet) ─────────────

def build_trait_index(*trait_lists: list[tuple[str, str]]) -> dict[str, str]:
    """Build a {normalized name -> sheet key} index from (key, label) pairs.

    Indexes both the human label ("Blood Sorcery") and the bare key suffix
    ("blood sorcery" from "disc_blood_sorcery") so players can type either."""
    index: dict[str, str] = {}
    for traits in trait_lists:
        for key, label in traits:
            index[label.strip().lower()] = key
            suffix = key.split("_", 1)[-1].replace("_", " ").strip().lower()
            index.setdefault(suffix, key)
    return index


def reroll_failures(normal_dice: list[int], hunger_dice: list[int],
                    difficulty: int = 0, count: int = 3,
                    rng: random.Random | None = None) -> tuple[RollResult, int]:
    """V5 Willpower reroll: reroll up to ``count`` regular (non-Hunger) dice
    that are failures (showing < 6). Hunger dice are never rerolled and
    successes are kept (rerolling a failure can only help). Returns the new
    ``RollResult`` and the number of dice actually rerolled."""
    r = rng or random
    count = max(0, int(count))
    normal = list(normal_dice)
    # Failures, lowest first (all equivalent in expectation; deterministic).
    failures = sorted((i for i, d in enumerate(normal) if d < 6),
                      key=lambda i: normal[i])
    chosen = failures[:count]
    for i in chosen:
        normal[i] = r.randint(1, 10)
    return classify(normal, list(hunger_dice), difficulty), len(chosen)


def rouse_check(count: int = 1,
                rng: random.Random | None = None) -> tuple[list[int], int]:
    """Roll ``count`` Rouse Check dice. Each die showing 6+ avoids a Hunger
    gain; 1-5 gains 1 Hunger. Returns ``(rolls, hunger_gained)``."""
    r = rng or random
    count = max(1, min(int(count), 5))
    rolls = [r.randint(1, 10) for _ in range(count)]
    gained = sum(1 for d in rolls if d < 6)
    return rolls, gained


def resolve_pool(expression: str, sheet: dict,
                 trait_index: dict[str, str]) -> tuple[int, list[tuple[str, int]], list[str]]:
    """Parse a pool expression into a total dice count.

    Tokens are separated by ``+`` and may be either a (possibly negative)
    integer modifier or a trait name resolved from the sheet via
    ``trait_index``. Returns ``(pool, parts, unknown)`` where ``parts`` is a
    list of ``(label, value)`` for display and ``unknown`` lists any tokens
    that didn't resolve.
    """
    pool = 0
    parts: list[tuple[str, int]] = []
    unknown: list[str] = []
    for raw in (expression or "").split("+"):
        tok = raw.strip()
        if not tok:
            continue
        # Flat numeric modifier (e.g. "2", "-1").
        if tok.lstrip("-").isdigit():
            val = int(tok)
            pool += val
            parts.append((f"{'+' if val >= 0 else ''}{val}", val))
            continue
        key = trait_index.get(tok.lower())
        if key is None:
            unknown.append(tok)
            continue
        val = int(sheet.get(key, 0) or 0)
        pool += val
        parts.append((tok.title(), val))
    return max(0, pool), parts, unknown
