CREATE TABLE IF NOT EXISTS skills (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    category    TEXT CHECK (category IS NULL OR category IN ('ai','messaging','notes','productivity','dev','security','media','data','company')),
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    official_source_id    TEXT,
    -- Trazabilidad de solo escritura; se lee por
    -- resource_source_links.component_key. Ver agents.sql.
    official_component_id TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_skills_official ON skills(official_source_id);
