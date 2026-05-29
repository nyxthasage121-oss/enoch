-- 019_hunting_site_sect_control.sql
--
-- Per Steward UX feedback (2026-05): hunting sites need to surface the
-- in-game sect that holds influence over the area, so players know what
-- they're walking into. This is a free-text field for now — chronicle
-- sect names vary (Camarilla, Anarch Movement, Sabbat, Hecata, Unbound,
-- bespoke factions) and we'd rather not constrain it with an enum.
--
-- We also re-label the "borough" column as "Area" in the UI without
-- renaming the column itself (avoids breaking foreign keys / migrations
-- already referencing it). The eventual chronicle_areas table will
-- replace this with a structured lookup once the chronicle has more
-- than the default NYC borough list.

ALTER TABLE hunting_sites ADD COLUMN sect_control TEXT;
