-- 004_character_review_state.sql
-- Track when staff opened the review for a pending character. Until this
-- is set, the player can revise their submission. Once set, the sheet
-- becomes read-only until staff approves or rejects.
--
-- NULL = not yet under review (player can still edit).

ALTER TABLE characters ADD COLUMN review_started_at TEXT;
ALTER TABLE characters ADD COLUMN review_started_by TEXT;
