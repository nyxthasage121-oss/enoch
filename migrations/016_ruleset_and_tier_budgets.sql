-- 016_ruleset_and_tier_budgets.sql
-- Replace the binary use_homebrew_rules toggle with a three-way
-- ruleset selector + per-tier budget overrides stored as JSON.
--
-- active_ruleset values: 'standard' | 'homebrew' | 'in_memoriam'
-- homebrew_tier_budgets shape:
-- {
--   "mortal":    {"xp": N, "merits": N, "advantages": N, "backgrounds": N, "flaw_cap": N},
--   "ghoul":     {...},
--   "revenant":  {...},
--   "thinblood": {...},
--   "neonate":   {...},
--   "ancilla":   {...}
-- }

ALTER TABLE chronicle_settings ADD COLUMN active_ruleset       TEXT NOT NULL DEFAULT 'standard';
ALTER TABLE chronicle_settings ADD COLUMN homebrew_tier_budgets TEXT;

-- Pull the old flag forward — chronicles that had homebrew on become
-- 'homebrew' on the new ruleset selector. Standard/off stays standard.
UPDATE chronicle_settings
SET active_ruleset = 'homebrew'
WHERE use_homebrew_rules = 1;
