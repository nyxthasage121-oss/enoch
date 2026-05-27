"""db.py — Database connection and migration runner.

Uses libsql-experimental which mirrors the sqlite3 API for local files
and speaks HTTP to Turso for production. Chunk 1: connection + migrations only.
Query helper functions are added in subsequent chunks.
"""
import logging
from contextlib import contextmanager
from pathlib import Path

import libsql_experimental as libsql

from .config import settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


# ── Connection ───────────────────────────────────────────────────

def _connect() -> libsql.Connection:
    url   = settings.DATABASE_URL
    token = settings.TURSO_AUTH_TOKEN
    if url.startswith("libsql") or url.startswith("https://"):
        return libsql.connect(database=url, auth_token=token)
    return libsql.connect(database=url)


@contextmanager
def get_db():
    """Yield a libsql connection; commit on success, rollback on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ── Migrations ───────────────────────────────────────────────────

def run_migrations() -> None:
    """Apply any pending numbered *.sql files from the migrations/ directory."""
    # Ensure the tracking table exists
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

    # Collect already-applied filenames
    with get_db() as conn:
        applied = {
            row[0]
            for row in conn.execute("SELECT filename FROM _migrations").fetchall()
        }

    # Apply pending files in lexicographic order (001_, 002_, …)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        log.info("Applying migration: %s", path.name)
        sql = path.read_text(encoding="utf-8")
        # Split on ';' to execute statement-by-statement
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        with get_db() as conn:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO _migrations (filename) VALUES (?)", (path.name,)
            )
        log.info("Applied: %s", path.name)
