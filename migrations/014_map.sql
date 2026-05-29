-- 014_map.sql
-- Chronicle map system. Layers group related geographic features and
-- carry display/visibility metadata; features are the actual geometry
-- the player sees on the Leaflet map. Designed chronicle-agnostic so
-- the same schema works for NYC, London, or a custom city.

CREATE TABLE IF NOT EXISTS map_layers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT,
    -- CSS hex (#RRGGBB) — applied to polygon fills + pin badges.
    color           TEXT    NOT NULL DEFAULT '#8B1A1A',
    -- Visibility tier: 'public' shows on /map for any signed-in player;
    -- 'staff' only renders on /staff/map.
    visibility      TEXT    NOT NULL DEFAULT 'public',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS map_features (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    layer_id        INTEGER NOT NULL REFERENCES map_layers(id) ON DELETE CASCADE,
    label           TEXT    NOT NULL DEFAULT '',
    description     TEXT,
    -- Chronicle-defined free-text tag (e.g. "elysium", "haven", "rival-domain").
    tag             TEXT,
    -- 'point' | 'polygon' | 'line'
    feature_type    TEXT    NOT NULL,
    -- GeoJSON Geometry object (without the Feature wrapper).
    geometry_json   TEXT    NOT NULL,
    -- Optional cross-refs — nullable. Set when the feature represents
    -- a coterie's territory, a hunting site location, or similar.
    coterie_id      INTEGER REFERENCES coteries(id),
    site_id         INTEGER REFERENCES hunting_sites(id),
    -- Per-feature visibility: even on a public layer, individual
    -- features can be staff-only (e.g. hidden chantries).
    is_hidden       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_map_features_layer ON map_features(layer_id);
CREATE INDEX IF NOT EXISTS idx_map_features_site  ON map_features(site_id);
CREATE INDEX IF NOT EXISTS idx_map_features_co    ON map_features(coterie_id);
