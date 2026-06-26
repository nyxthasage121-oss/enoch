-- 059_directory.sql
-- Character directory (chronicle-wide IC "who's who"). Approved characters
-- appear by default; this flag lets a player hide one of theirs from the
-- public roster + profile pages. profile_blurb (already present) is reused as
-- the IC bio shown on the public profile.
ALTER TABLE characters ADD COLUMN directory_hidden INTEGER NOT NULL DEFAULT 0;
