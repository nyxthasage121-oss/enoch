-- 009_drafts_and_budgets.sql
-- Phase 2:
--   1. Drafts — characters can be saved partway through the wizard
--      without going to staff. Drafts skip the pending-approval queue
--      until the player explicitly submits.
--   2. Submission notes — free-text "anything for the ST" on the final
--      wizard step, persisted on the character row.
--   3. Background dot budget + Flaw dot cap — completing the chronicle's
--      homebrew rules set so the wizard sidebar can show all four budgets.

ALTER TABLE characters ADD COLUMN is_draft           INTEGER NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN submission_notes   TEXT;

ALTER TABLE chronicle_settings ADD COLUMN homebrew_background_budget INTEGER NOT NULL DEFAULT 5;
ALTER TABLE chronicle_settings ADD COLUMN homebrew_flaw_cap          INTEGER NOT NULL DEFAULT 2;
