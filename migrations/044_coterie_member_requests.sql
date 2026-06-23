-- 044_coterie_member_requests.sql
-- Leader-proposed additions of a character to an EXISTING coterie, pending
-- staff approval. Mirrors the coterie_requests (formation) flow, but targets a
-- live coterie + a single character; on approval staff run add_coterie_member.
CREATE TABLE IF NOT EXISTS coterie_member_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    coterie_id    INTEGER NOT NULL REFERENCES coteries(id),
    character_id  INTEGER NOT NULL REFERENCES characters(id),
    requested_by  TEXT    NOT NULL,
    note          TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending',
    submitted_at  TEXT    NOT NULL,
    reviewed_by   TEXT,
    reviewed_at   TEXT,
    review_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_coterie_member_requests_status
    ON coterie_member_requests(status);
CREATE INDEX IF NOT EXISTS idx_coterie_member_requests_coterie
    ON coterie_member_requests(coterie_id);
