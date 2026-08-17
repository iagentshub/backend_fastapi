CREATE TABLE IF NOT EXISTS llm_orchestration_bindings (
    orchestration_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(orchestration_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_llm_orchestration_bindings_user
    ON llm_orchestration_bindings(user_id, updated_at DESC);
