-- 002_char_rejection.sql — Add rejection tracking to characters table
ALTER TABLE characters ADD COLUMN rejection_reason TEXT;
ALTER TABLE characters ADD COLUMN rejected_at TEXT;
