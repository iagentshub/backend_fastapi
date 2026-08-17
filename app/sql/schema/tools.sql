CREATE TABLE IF NOT EXISTS tools (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    language    TEXT NOT NULL DEFAULT 'python' CHECK (language IN ('python','shell','cpp')),
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    binary_b64         TEXT,
    binary_filename    TEXT,
    binary_size        INTEGER,
    binary_uploaded_at TEXT,
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    official_source_id    TEXT,
    -- Trazabilidad de solo escritura. La lectura viva es
    -- resource_source_links.component_key. Ver agents.sql.
    official_component_id TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_tools_owner ON tools(owner_id, scope, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tools_official ON tools(official_source_id);
