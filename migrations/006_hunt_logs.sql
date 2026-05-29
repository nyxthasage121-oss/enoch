-- 006_hunt_logs.sql
-- Log every hunt at a site: who hunted there, what happened, and when.
-- Source distinguishes player-submitted ('web') from dice-bot-pushed ('bot')
-- so staff can tell at a glance whether a roll backed up the outcome.

CREATE TABLE IF NOT EXISTS hunt_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id       INTEGER NOT NULL REFERENCES hunting_sites(id),
    character_id  INTEGER NOT NULL REFERENCES characters(id),
    outcome       TEXT    NOT NULL CHECK (outcome IN
                          ('clean', 'success', 'messy_critical', 'bestial_failure')),
    note          TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL DEFAULT 'web'
                          CHECK (source IN ('web', 'bot')),
    hunted_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_hunt_logs_site
    ON hunt_logs (site_id, hunted_at DESC);

CREATE INDEX IF NOT EXISTS ix_hunt_logs_character
    ON hunt_logs (character_id, hunted_at DESC);
