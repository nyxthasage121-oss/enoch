-- 042_app_alerts.sql
-- Operational alert log: persisted warn/error entries from the web app (unhandled
-- 500s) and the bot (reported via POST /api/alerts), surfaced on a dismissable
-- staff page so silent failures don't go unnoticed. NULL dismissed_at == active.

CREATE TABLE IF NOT EXISTS app_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL DEFAULT 'web',    -- web | bot
    level        TEXT    NOT NULL DEFAULT 'error',   -- warn | error
    event        TEXT    NOT NULL DEFAULT '',        -- short tag, e.g. 'unhandled'
    message      TEXT    NOT NULL DEFAULT '',        -- one-line summary
    detail       TEXT    NOT NULL DEFAULT '',        -- traceback / extra context
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    dismissed_at TEXT,
    dismissed_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_alerts_active ON app_alerts(dismissed_at, created_at);
