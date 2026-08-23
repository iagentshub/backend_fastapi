-- Estado único de ejecución para agentes y workflows.

-- name: canonical_ref
SELECT owner, linked_to_user, linked_to_id
FROM resource_social
WHERE resource_type=? AND resource_id=?
ORDER BY CASE WHEN owner=? THEN 0 ELSE 1 END
LIMIT 1;

-- name: purge_stale
DELETE FROM resource_executions WHERE heartbeat_at < ?;

-- name: purge_stale_conflict
DELETE FROM resource_executions
WHERE execution_key=? AND heartbeat_at < ?;

-- name: insert_pg
INSERT INTO resource_executions (
    execution_key, execution_id, resource_type, canonical_owner,
    canonical_resource_id, local_resource_id, started_by, run_id,
    heartbeat_at, created_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (execution_key) DO NOTHING;

-- name: insert_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO resource_executions (
    execution_key, execution_id, resource_type, canonical_owner,
    canonical_resource_id, local_resource_id, started_by, run_id,
    heartbeat_at, created_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: get_by_execution_id
SELECT * FROM resource_executions WHERE execution_id=?;

-- name: list_for_user_with_aliases
SELECT e.*, social.resource_id AS alias_resource_id
FROM resource_executions e
LEFT JOIN resource_social social
  ON social.resource_type=e.resource_type
 AND social.owner IN (?, ?)
 AND (
   (social.linked_to_user=e.canonical_owner
    AND social.linked_to_id=e.canonical_resource_id)
   OR (social.owner=e.canonical_owner
       AND social.resource_id=e.canonical_resource_id)
 )
WHERE e.started_by=? AND e.heartbeat_at>=?
ORDER BY e.created_at DESC;

-- name: touch
UPDATE resource_executions SET heartbeat_at=?
WHERE execution_key=? AND execution_id=?;

-- name: release
DELETE FROM resource_executions
WHERE execution_key=? AND execution_id=?;

-- name: release_run
DELETE FROM resource_executions WHERE run_id=?;
