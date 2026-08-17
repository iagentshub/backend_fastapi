-- Consultas de app/storage/workflows.py.

-- name: list_by_owner
SELECT *
FROM agent_workflows
WHERE owner_id=?
ORDER BY updated_at DESC;

-- name: get_owned
SELECT *
FROM agent_workflows
WHERE id=? AND owner_id=?;

-- name: get_any
SELECT *
FROM agent_workflows
WHERE id=?
ORDER BY updated_at DESC
LIMIT 1;

-- name: list_all
SELECT *
FROM agent_workflows
ORDER BY updated_at DESC;

-- name: upsert
INSERT INTO agent_workflows (id, owner_id, name, description, definition, scope, labels, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id, owner_id) DO
UPDATE
SET name=excluded.name, description=excluded.description, definition=excluded.definition, scope=excluded.scope, labels=excluded.labels, updated_at=excluded.updated_at;

-- name: exists_owned
SELECT 1
FROM agent_workflows
WHERE id=? AND owner_id=?;

-- name: delete_owned
DELETE FROM agent_workflows
WHERE id=? AND owner_id=?;

-- name: delete_shares
DELETE FROM resource_group_shares
WHERE resource_type='workflow' AND resource_id=?;

-- name: exists_any
SELECT 1
FROM agent_workflows
WHERE id=?;

-- name: delete_any
DELETE FROM agent_workflows
WHERE id=?;
