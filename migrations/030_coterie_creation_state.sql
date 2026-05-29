-- 030_coterie_creation_state.sql
--
-- Coterie creation lifecycle (Feature C, phase 3a). A coterie is assembled
-- while 'forming' (members/leader allocate free dots, advantages, flaws), then
-- one member submits it and staff sign off to make it 'active'. Creation-time
-- allocation (free dots, the flaw budget) is only open while 'forming'.
--
-- This is distinct from `status` (active|disbanded), which is the operational
-- lifecycle. Existing coteries default to 'active' (already finalised) so
-- nothing in flight gets stuck mid-creation.

ALTER TABLE coteries ADD COLUMN creation_state TEXT NOT NULL DEFAULT 'active';
