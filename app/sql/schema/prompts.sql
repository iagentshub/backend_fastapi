CREATE TABLE IF NOT EXISTS prompts (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    alias       TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_prompts_owner ON prompts(owner_id, scope, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_prompts_official ON prompts(official_source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_alias_owner ON prompts(owner_id, alias);
