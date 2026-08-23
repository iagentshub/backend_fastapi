CREATE TABLE IF NOT EXISTS resource_executions (
    execution_key TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL,
    canonical_owner TEXT NOT NULL,
    canonical_resource_id TEXT NOT NULL,
    local_resource_id TEXT NOT NULL,
    started_by TEXT NOT NULL,
    run_id TEXT,
    heartbeat_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resource_executions_user
    ON resource_executions(started_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_resource_executions_heartbeat
    ON resource_executions(heartbeat_at);

