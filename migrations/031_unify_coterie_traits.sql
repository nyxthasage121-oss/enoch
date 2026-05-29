-- 031_unify_coterie_traits.sql
--
-- Fold the legacy coterie_merits / coterie_flaws tables into the unified
-- coterie_contributions model (migration 020) so staff and players see ONE
-- coterie sheet and the C3a sign-off reviews what was actually assembled.
--
-- Existing legacy rows are copied across once. The legacy tables are left in
-- place (now unused by the app) rather than dropped — a destructive DROP in
-- SQLite would require a table rebuild and risks data loss; leaving them empty
-- and unreferenced is harmless.

INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, xp_paid, note, created_at, updated_at)
SELECT cm.coterie_id, cm.character_id,
       CASE cm.merit_type WHEN 'donated'  THEN 'donated'
                          WHEN 'creation' THEN 'creation_free'
                          ELSE 'staff_grant' END,
       'merit', cm.merit_name, cm.dots, 'active', 0,
       'Migrated from legacy coterie_merits', cm.created_at, cm.created_at
FROM coterie_merits cm;

INSERT INTO coterie_contributions
    (coterie_id, character_id, contribution_type, target_kind, target_name,
     dots, status, xp_paid, note, created_at, updated_at)
SELECT cf.coterie_id, NULL, 'flaw_bonus', 'flaw', cf.flaw_name, cf.dots,
       'active', 0, 'Migrated from legacy coterie_flaws', cf.created_at, cf.created_at
FROM coterie_flaws cf;
