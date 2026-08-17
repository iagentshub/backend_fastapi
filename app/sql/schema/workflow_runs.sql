CREATE TABLE IF NOT EXISTS workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    started_by      TEXT NOT NULL,
    group_id        TEXT NOT NULL,
    workflow_name   TEXT NOT NULL,
    definition      TEXT NOT NULL,
    agents          TEXT NOT NULL DEFAULT '[]',
    input           TEXT NOT NULL,
    status          TEXT NOT NULL,
    completed_steps INTEGER NOT NULL DEFAULT 0,
    total_steps     INTEGER NOT NULL DEFAULT 0,
    active_node_id  TEXT,
    final_output    TEXT,
    error           TEXT,
    last_sequence   INTEGER NOT NULL DEFAULT 0,
    heartbeat_at    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_user
    ON workflow_runs(started_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
    ON workflow_runs(status, heartbeat_at);
