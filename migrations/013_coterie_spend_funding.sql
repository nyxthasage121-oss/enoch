-- 013_coterie_spend_funding.sql
-- Generalize coterie_spends so a coterie can group-buy any kind of trait
-- (domain, coterie merit, free-form), and add the per-member funding step
-- the old NYbN flow used. Status now has 4 values:
--   pending  → proposal stage; members can commit their share
--   funded   → every member committed; ready for staff approval
--   approved → staff approved + XP deducted
--   rejected → staff rejected (or proposer cancelled)

ALTER TABLE coterie_spends ADD COLUMN spend_category TEXT NOT NULL DEFAULT 'domain';
ALTER TABLE coterie_spends ADD COLUMN initiated_by   TEXT;
ALTER TABLE coterie_spends ADD COLUMN justification  TEXT;
ALTER TABLE coterie_spends ADD COLUMN notes          TEXT;

-- Existing rows have current_dots/new_dots filled (domain upgrades); leave
-- those as-is. For non-domain categories we'll store 0/0 — meaningless
-- but the columns are NOT NULL in the original schema and rewriting that
-- in SQLite would require a full table copy.
