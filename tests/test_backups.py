"""Automated off-site backups — the due-check, the shared export builder, the
refactored export.json download, and run_backup_sweep with a mocked webhook."""
from datetime import datetime, timedelta, timezone


def test_is_due_logic():
    from web.backups import _is_due
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    assert _is_due(None, now) is True               # never backed up
    assert _is_due("not-a-date", now) is True        # unparseable → treat as due
    recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_due(recent, now) is False             # 2h ago → not due
    old = (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_due(old, now) is True                 # 30h ago → due


def test_build_export_snapshot_covers_all_tables(player):
    from web.db import EXPORT_TABLES, build_export_snapshot, get_db
    with get_db() as conn:
        snap = build_export_snapshot(conn, "tester")
    assert snap["exported_by"] == "tester"
    assert snap["schema_version"] == 1
    assert set(snap["tables"]) == set(EXPORT_TABLES)
    assert "characters" in snap["tables"]


def test_export_download_still_works(staff):
    """The refactored export.json route streams a valid JSON snapshot."""
    import json
    r = staff.get("/staff/admin/export.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    data = json.loads(r.text)
    assert data["schema_version"] == 1 and "tables" in data


def test_backup_now_without_webhook_redirects(staff):
    """Back up now with no webhook configured → redirect with a flash, no crash
    and no network call."""
    from web.db import get_db, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="test", backup_webhook_url="")
    r = staff.post("/staff/admin/backup-now", data={"_csrf": "dev-csrf-token"},
                   follow_redirects=False)
    assert r.status_code == 303


def test_run_backup_sweep_posts_and_marks(staff, monkeypatch):
    """With a webhook set + forced, run_backup_sweep builds a gzipped snapshot,
    'posts' it (mocked), and records last_backup_at."""
    import asyncio

    from web import backups
    from web.db import get_db, get_settings, upsert_settings
    with get_db() as conn:
        upsert_settings(conn, actor_id="test",
                        backup_webhook_url="https://discord.test/api/webhooks/x/y")

    captured = {}

    async def _fake_post(url, filename, gz, now):
        captured.update(url=url, filename=filename, size=len(gz))
        return None   # success

    monkeypatch.setattr(backups, "_post_webhook", _fake_post)
    result = asyncio.run(backups.run_backup_sweep(force=True))

    assert result["ok"] is True
    assert captured["url"].startswith("https://discord.test/")
    assert captured["filename"].endswith(".json.gz") and captured["size"] > 0
    with get_db() as conn:
        assert get_settings(conn).get("last_backup_at")
        upsert_settings(conn, actor_id="test", backup_webhook_url="")  # tidy up
