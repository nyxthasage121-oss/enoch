"""sheet_migrations.py — Versioned migrations for the in-blob sheet_json.

Sheets are stored as a JSON blob on `characters.sheet_json`. Their shape
evolves over time (new V5 traits added, structures normalized, etc.).
Without versioning, an old character row silently corrupts when the
reader assumes the new shape.

Pattern (lifted from the MCbN tracker's `applyCharacterCompatibilityPatches`
chain): every sheet carries a `_schema_version` field. On read, we walk
a chain of patches from the stored version up to `CURRENT_SHEET_VERSION`.
Each patch is idempotent and side-effect free. Sheets without the field
are treated as version 0 (the original chargen shape).

Adding a new version:
  1. Bump `CURRENT_SHEET_VERSION`.
  2. Write `_patch_v{N}_to_v{N+1}(sheet) -> sheet`.
  3. Add to `_MIGRATIONS`.
  4. Write a regression test in tests/test_sheet_migrations.py.

Migrations must:
  * Never raise on input that's already the target version.
  * Never destroy data — at most rename, default, or normalize.
  * Be deterministic (no datetime, no random).
"""
from __future__ import annotations

from typing import Callable

CURRENT_SHEET_VERSION = 2

# Sentinel for "legacy sheet" — was missing _schema_version. We treat as v0.
_VERSION_KEY = "_schema_version"


def _patch_v0_to_v1(sheet: dict) -> dict:
    """v0 → v1: stamp the version field. No structural changes.

    v0 is the pre-versioning shape — V5 attributes/skills/disciplines as
    flat keys, lists for merits/flaws/etc. v1 is functionally identical;
    the bump exists so the next real change (v1 → v2) has somewhere to
    hook in. Idempotent on already-v1 sheets."""
    sheet[_VERSION_KEY] = 1
    return sheet


def _patch_v1_to_v2(sheet: dict) -> dict:
    """v1 → v2: drop the legacy `_post_wizard` routing sentinel. It was a
    UI/routing flag (does this draft resume on the detail page or back in the
    wizard?) that never belonged in the character sheet; it now lives in the
    characters.post_wizard column (migration 026). Idempotent — a no-op on
    sheets that never carried it."""
    sheet.pop("_post_wizard", None)
    sheet[_VERSION_KEY] = 2
    return sheet


_MIGRATIONS: list[tuple[int, Callable[[dict], dict]]] = [
    (0, _patch_v0_to_v1),
    (1, _patch_v1_to_v2),
]


def sheet_version(sheet: dict | None) -> int:
    """Return the stored version, defaulting to 0 for legacy sheets."""
    if not isinstance(sheet, dict):
        return 0
    v = sheet.get(_VERSION_KEY, 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def migrate_sheet(sheet: dict | None) -> dict:
    """Run the migration chain from the stored version up to current.

    Always returns a dict (empty if input was None or non-dict). Mutates
    the passed-in dict in place for efficiency, and also returns it so
    callers can chain. Already-current sheets pass through with one
    cheap dict.get() check."""
    if not isinstance(sheet, dict):
        return {_VERSION_KEY: CURRENT_SHEET_VERSION}
    v = sheet_version(sheet)
    if v >= CURRENT_SHEET_VERSION:
        return sheet
    for from_v, patch in _MIGRATIONS:
        if from_v < v:
            continue
        sheet = patch(sheet)
    return sheet
