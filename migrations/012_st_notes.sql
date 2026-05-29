-- 012_st_notes.sql
-- Staff-private notes on a character. Distinct from submission_notes
-- (player → staff) and backstory (player-written, staff-private RP context).
-- This column is for staff working notes only and is never shown to players.

ALTER TABLE characters ADD COLUMN st_notes TEXT;
