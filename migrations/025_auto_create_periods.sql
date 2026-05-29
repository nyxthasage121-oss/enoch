-- 025_auto_create_periods.sql
--
-- Chronicle-wide toggle for automatic next-period generation.
--
-- When enabled, auto_create_next_period_if_due() infers the chronicle's
-- rhythm straight from history — cadence = the gap between the last two
-- periods' opens_at, duration = the latest period's own open window — and
-- stamps the next "Night N+1" shortly before it is due. It keeps exactly
-- one period on deck and never auto-activates (staff still press Activate).
--
-- No stored schedule template required; staff just keep opening periods
-- normally and the system learns the cadence. Off by default — opt in per
-- chronicle from the Admin -> Periods tab.

ALTER TABLE chronicle_settings ADD COLUMN auto_create_periods_enabled INTEGER NOT NULL DEFAULT 0;
