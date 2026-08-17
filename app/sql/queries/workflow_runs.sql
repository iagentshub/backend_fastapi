-- Consultas de app/storage/workflow_runs.py.

-- name: insert_run
INSERT INTO workflow_runs (id, workflow_id, started_by, group_id, workflow_name, definition, agents, input, status, total_steps, heartbeat_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?);

-- name: get_run
SELECT *
FROM workflow_runs
WHERE id=?;

-- name: get_run_owned
SELECT *
FROM workflow_runs
WHERE id=? AND started_by=?;

-- name: list_active_runs
SELECT *
FROM workflow_runs
WHERE started_by=? AND status IN ('queued','running','cancelling')
ORDER BY created_at DESC;

-- name: list_finished_runs
SELECT *
FROM workflow_runs
WHERE started_by=? AND status IN ('cancelled','completed','failed')
ORDER BY created_at DESC
LIMIT ?;

-- name: update_status
UPDATE workflow_runs
SET status=?, error=COALESCE(?, error), final_output=COALESCE(?, final_output), heartbeat_at=?, updated_at=?, started_at=COALESCE(started_at, ?), finished_at=COALESCE(?, finished_at)
WHERE id=?;

-- name: mark_running
UPDATE workflow_runs
SET status='running', started_at=?, heartbeat_at=?, updated_at=?
WHERE id=? AND status='queued';

-- name: heartbeat
UPDATE workflow_runs
SET heartbeat_at=?, updated_at=?
WHERE id=?;

-- name: progress_of
SELECT last_sequence, completed_steps, total_steps
FROM workflow_runs
WHERE id=?;

-- name: insert_event
INSERT INTO workflow_run_events (run_id, sequence, payload, created_at)
VALUES (?, ?, ?, ?);

-- name: update_progress
UPDATE workflow_runs
SET last_sequence=?, completed_steps=?, active_node_id=?, heartbeat_at=?, updated_at=?
WHERE id=?;

-- name: list_events_since
SELECT sequence, payload, created_at
FROM workflow_run_events
WHERE run_id=? AND sequence>?
ORDER BY sequence;

-- name: finished_before
SELECT id
FROM workflow_runs
WHERE status IN ('cancelled','completed','failed') AND finished_at<?;

-- name: distinct_users
SELECT DISTINCT started_by
FROM workflow_runs;

-- name: finished_by_user
SELECT id
FROM workflow_runs
WHERE started_by=? AND status IN ('cancelled','completed','failed')
ORDER BY created_at DESC;

-- name: delete_events
DELETE FROM workflow_run_events
WHERE run_id=?;

-- name: delete_run
DELETE FROM workflow_runs
WHERE id=?;

-- name: stale_runs
SELECT id
FROM workflow_runs
WHERE status IN ('queued','running','cancelling') AND heartbeat_at<?;

-- name: mark_failed
UPDATE workflow_runs
SET status='failed', error=?, finished_at=?, updated_at=?
WHERE id=?;
