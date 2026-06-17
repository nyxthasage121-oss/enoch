-- 038_recompute_coterie_domain.sql
-- One-time data fix: re-derive every coterie's cached domain (chasse / lien /
-- portillon) from its active contributions. This zeroes any spurious auto-Chasse
-- that was written straight to the cache column with no backing contribution
-- (the old create-default / staff-form bug); coteries whose domain is backed by
-- real contributions keep their correct, cap-5 total. Mirrors
-- _recompute_coterie_ratings(): MIN(5, SUM of active contributions).

UPDATE coteries SET
    chasse = MIN(5, COALESCE((SELECT SUM(dots) FROM coterie_contributions cc
                  WHERE cc.coterie_id = coteries.id
                    AND cc.target_kind = 'chasse' AND cc.status = 'active'), 0)),
    lien = MIN(5, COALESCE((SELECT SUM(dots) FROM coterie_contributions cc
                WHERE cc.coterie_id = coteries.id
                  AND cc.target_kind = 'lien' AND cc.status = 'active'), 0)),
    portillon = MIN(5, COALESCE((SELECT SUM(dots) FROM coterie_contributions cc
                     WHERE cc.coterie_id = coteries.id
                       AND cc.target_kind = 'portillon' AND cc.status = 'active'), 0));
