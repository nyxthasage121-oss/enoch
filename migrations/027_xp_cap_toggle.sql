-- 027_xp_cap_toggle.sql
--
-- Chronicle-wide toggle for whether the XP cap is enforced at all.
--
-- NYbN's house rule caps characters at 350 XP (per-character `characters.xp_cap`),
-- and on hitting it the retirement window opens. Some chronicles don't want a
-- cap. This flag gates that behavior: when enabled (the default, = current
-- behavior), claim approval awards up to the cap and opens the retirement
-- window; when disabled, claims award in full and the cap never auto-triggers
-- retirement, and the UI hides the "/cap" figure.

ALTER TABLE chronicle_settings ADD COLUMN xp_cap_enabled INTEGER NOT NULL DEFAULT 1;
