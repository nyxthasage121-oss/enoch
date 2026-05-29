-- 011_chargen_completeness.sql
-- Fill in V5 RAW chargen requirements + common sheet metadata that the
-- wizard was missing:
--   - Ambition (long-term goal) + Desire (short-term goal)
--   - Profession (day-job / cover identity)
--   - True Age / Apparent Age — common V5 sheet fields
--   - Pronouns
--   - Backstory (staff-private long-form, separate from concept/blurb)
--
-- Initial Blood Potency / Humanity / Hunger live inside sheet_json,
-- so no schema change for those — handled in the wizard route.

ALTER TABLE characters ADD COLUMN ambition       TEXT;
ALTER TABLE characters ADD COLUMN desire         TEXT;
ALTER TABLE characters ADD COLUMN profession     TEXT;
ALTER TABLE characters ADD COLUMN true_age       INTEGER;
ALTER TABLE characters ADD COLUMN apparent_age   INTEGER;
ALTER TABLE characters ADD COLUMN pronouns       TEXT;
ALTER TABLE characters ADD COLUMN backstory      TEXT;
