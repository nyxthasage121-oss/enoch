-- 029_coterie_proposal_fields.sql
--
-- Coterie proposal wizard (Feature C, phase 1). The formation request now
-- captures two extra things:
--   * members_acquainted — the proposing player confirms their characters
--     know and have met each other in-character (a gate in the wizard).
--   * requested_site_id   — which hunting site the coterie wants to occupy.
--
-- On approval the requested site is linked to the new coterie
-- (hunting_sites.coterie_id) when it isn't already controlled. FK enforcement
-- is off chronicle-wide, so requested_site_id is a plain nullable int.

ALTER TABLE coterie_requests ADD COLUMN members_acquainted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE coterie_requests ADD COLUMN requested_site_id INTEGER;
