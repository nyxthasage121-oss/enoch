"""Automated off-site backups.

A full chronicle snapshot (every table in ``db.EXPORT_TABLES``) is JSON-dumped,
gzipped, and POSTed to a Discord webhook so the data lives somewhere other than
the single Turso DB. The hourly sweep calls :func:`run_backup_sweep` once a day
(when ``backup_webhook_url`` is set); the Admin "Back up now" button calls it
with ``force=True``. No bot dependency — the web app posts the file directly.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

_MIN_HOURS = 23   # daily-ish; 23h so slight sweep drift never skips a day
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _is_due(last_backup_at: str | None, now: datetime, min_hours: int = _MIN_HOURS) -> bool:
    if not last_backup_at:
        return True
    try:
        last = datetime.strptime(last_backup_at, _TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return (now - last).total_seconds() >= min_hours * 3600


def _build_gz(conn, now: datetime) -> tuple[str, bytes]:
    from .db import build_export_snapshot
    payload = build_export_snapshot(conn, exported_by="backup")
    body = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
    filename = f"enoch-backup-{now.strftime('%Y-%m-%d')}.json.gz"
    return filename, gzip.compress(body)


async def _post_webhook(webhook_url: str, filename: str, gz: bytes, now: datetime) -> str | None:
    """POST the gzipped snapshot to a Discord webhook. Returns None on success
    or a short error string on failure."""
    import httpx
    note = (f"Enoch chronicle backup — {now.strftime('%Y-%m-%d %H:%M UTC')} "
            f"({len(gz) // 1024} KB compressed)")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                webhook_url,
                data={"payload_json": json.dumps({"content": note})},
                files={"files[0]": (filename, gz, "application/gzip")},
            )
        if resp.status_code >= 300:
            return f"Webhook returned HTTP {resp.status_code}: {resp.text[:160]}"
        return None
    except Exception as exc:   # network/URL errors — never crash the sweep
        return f"Webhook post failed: {exc}"


async def run_backup_sweep(*, force: bool = False) -> dict:
    """Build + post a backup if one is due (or ``force``). Returns a small
    result dict: ok / skipped / reason / filename / bytes. Safe to call from the
    hourly sweep — failures are reported, never raised."""
    from .db import get_db, get_settings, mark_backup_done
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        settings = get_settings(conn) or {}
        webhook = (settings.get("backup_webhook_url") or "").strip()
        if not webhook:
            return {"ok": False, "skipped": True, "reason": "No backup webhook is configured."}
        if not force and not _is_due(settings.get("last_backup_at"), now):
            return {"ok": False, "skipped": True, "reason": "A backup was already taken in the last day."}
        filename, gz = _build_gz(conn, now)

    err = await _post_webhook(webhook, filename, gz, now)
    if err:
        return {"ok": False, "skipped": False, "reason": err}

    with get_db() as conn:
        mark_backup_done(conn, now.strftime(_TS_FMT))
    return {"ok": True, "filename": filename, "bytes": len(gz)}
