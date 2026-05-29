"""Regression tests for the in-blob sheet_json migration chain.

Each version bump gets a test here. The chain must be:
  * idempotent on already-current sheets
  * safe on None / empty / wrong-shape input
  * non-destructive of existing data
"""
from web.sheet_migrations import (
    CURRENT_SHEET_VERSION,
    migrate_sheet,
    sheet_version,
    _VERSION_KEY,
)


def test_legacy_sheet_is_treated_as_v0():
    sheet = {"strength": 3, "auspex": 2}
    assert sheet_version(sheet) == 0


def test_migrate_stamps_current_version_on_legacy():
    sheet = {"strength": 3, "auspex": 2}
    migrated = migrate_sheet(sheet)
    assert migrated[_VERSION_KEY] == CURRENT_SHEET_VERSION
    # Data preserved
    assert migrated["strength"] == 3
    assert migrated["auspex"] == 2


def test_migrate_is_idempotent():
    sheet = {"strength": 3, _VERSION_KEY: CURRENT_SHEET_VERSION}
    once  = migrate_sheet(sheet)
    twice = migrate_sheet(once)
    assert once == twice
    assert twice[_VERSION_KEY] == CURRENT_SHEET_VERSION


def test_migrate_handles_none():
    out = migrate_sheet(None)
    assert isinstance(out, dict)
    assert out[_VERSION_KEY] == CURRENT_SHEET_VERSION


def test_migrate_handles_empty_dict():
    out = migrate_sheet({})
    assert out[_VERSION_KEY] == CURRENT_SHEET_VERSION


def test_migrate_handles_non_dict():
    # Defensive: a list or string shouldn't blow up the read path.
    assert migrate_sheet([1, 2, 3])[_VERSION_KEY] == CURRENT_SHEET_VERSION  # type: ignore[arg-type]


def test_migrate_handles_bogus_version_field():
    # If _schema_version is something unparseable, fall back to 0.
    sheet = {"strength": 3, _VERSION_KEY: "not-a-number"}
    assert sheet_version(sheet) == 0
    migrated = migrate_sheet(sheet)
    assert migrated[_VERSION_KEY] == CURRENT_SHEET_VERSION


def test_enrich_char_applies_migration_to_reads(player):
    """End-to-end: a character loaded via the player route should have
    a sheet_json that includes the schema version stamp."""
    from web.db import get_db, get_character
    with get_db() as conn:
        # Force the dev character's sheet back to legacy (no _schema_version)
        # so we can verify migrate_sheet runs on read.
        row = conn.execute(
            "SELECT id, sheet_json FROM characters WHERE name='Valeria Morano'"
        ).fetchone()
        cid = row["id"]
        import json as _j
        sheet = _j.loads(row["sheet_json"])
        sheet.pop(_VERSION_KEY, None)
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (_j.dumps(sheet), cid))
        conn.commit()

        ch = get_character(conn, cid)
    assert ch is not None
    assert ch["sheet_json"].get(_VERSION_KEY) == CURRENT_SHEET_VERSION
