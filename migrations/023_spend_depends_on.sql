-- 023_spend_depends_on.sql
--
-- Per-spend dependency chain (MCbN pattern). Lets a player batch-submit
-- a chain of related spends ("Dominate 1→2 then Dominate 2→3") in one
-- session without staff having to approve them sequentially in real
-- time. The dependent spend is blocked from approval until its parent
-- lands; if the parent is rejected, the dependent is auto-rejected with
-- a "parent rejected" reason.
--
-- Self-FK on spend_requests.id. NULL means "no dependency" (the common
-- case). The approve_spend route checks `depends_on` against the parent
-- row's status before flipping status to 'approved'.

ALTER TABLE spend_requests ADD COLUMN depends_on INTEGER REFERENCES spend_requests(id);

CREATE INDEX IF NOT EXISTS idx_spend_depends_on
    ON spend_requests(depends_on);
