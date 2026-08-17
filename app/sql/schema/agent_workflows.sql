CREATE TABLE IF NOT EXISTS agent_workflows (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition  TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'private',
    labels      TEXT NOT NULL DEFAULT '["private"]',
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    official_source_id    TEXT,
    -- Trazabilidad de solo escritura; se lee por
    -- resource_source_links.component_key. Ver agents.sql.
    official_component_id TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY(id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_workflows_owner
    ON agent_workflows(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_workflows_official
    ON agent_workflows(official_source_id);
