"""Safe parsing helpers for form / query input.

The web UI always sends well-formed numbers, but a hand-crafted or malformed
direct POST/GET (e.g. ``dots=abc``) used to blow up a bare ``int(...)`` with a
500. ``form_int`` parses defensively: it falls back to a default on missing or
non-numeric input and can clamp to a range, so a bad value degrades to a sane
default instead of crashing the request.
"""
from __future__ import annotations


def form_int(value, default: int = 0, *, lo: int | None = None, hi: int | None = None) -> int:
    """Parse ``value`` to an int, returning ``default`` on missing/non-numeric
    input. Optionally clamp the result to ``[lo, hi]`` (either bound optional).

    >>> form_int("3")
    3
    >>> form_int("abc", 5)
    5
    >>> form_int(None)
    0
    >>> form_int("99", 1, lo=1, hi=5)
    5
    >>> form_int("-2", lo=0)
    0
    """
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        n = default
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n
