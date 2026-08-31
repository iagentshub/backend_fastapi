CREATE TABLE IF NOT EXISTS llm_orchestrations (
    id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL,
    labels TEXT NOT NULL DEFAULT '["private"]',
    is_active @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_llm_orchestrations_owner
    ON llm_orchestrations(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_orchestrations_updated_page
    ON llm_orchestrations(updated_at DESC, id);
