-- 026_post_wizard_flag.sql
--
-- Move the post-wizard routing flag out of the sheet_json blob into a real
-- column. Short-form chargen marked "finished the wizard but still needs to
-- fill the full sheet" drafts with an `_post_wizard` sentinel *inside*
-- sheet_json — routing/UI state that never belonged in the character sheet.
-- This column replaces it.
--
-- Backfill from existing blobs so any in-flight draft keeps routing to the
-- sheet tab. The sentinel itself is stripped from blobs lazily by the
-- v1 -> v2 sheet migration in web/sheet_migrations.py.

ALTER TABLE characters ADD COLUMN post_wizard INTEGER NOT NULL DEFAULT 0;

UPDATE characters
   SET post_wizard = 1
 WHERE json_extract(sheet_json, '$._post_wizard') = 1;
