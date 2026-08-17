-- Consultas de app/storage/llm_orchestrations.py.

-- name: list_by_owner
SELECT *
FROM llm_orchestrations
WHERE owner_id=?
ORDER BY updated_at DESC;

-- name: list_all
SELECT *
FROM llm_orchestrations
ORDER BY updated_at DESC;

-- name: get_owned
SELECT *
FROM llm_orchestrations
WHERE id=? AND owner_id=?;

-- name: get_any
SELECT *
FROM llm_orchestrations
WHERE id=?
ORDER BY updated_at DESC
LIMIT 1;

-- name: upsert
INSERT INTO llm_orchestrations (id, owner_id, name, description, definition, labels, is_active, deactivated_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id, owner_id) DO
UPDATE
SET name=excluded.name, description=excluded.description, definition=excluded.definition, labels=excluded.labels, updated_at=excluded.updated_at;

-- name: exists_owned
SELECT 1
FROM llm_orchestrations
WHERE id=? AND owner_id=?;

-- name: delete_owned
DELETE FROM llm_orchestrations
WHERE id=? AND owner_id=?;

-- name: delete_bindings
DELETE FROM llm_orchestration_bindings
WHERE orchestration_id=?;

-- name: exists_any
SELECT 1
FROM llm_orchestrations
WHERE id=?;

-- name: delete_any
DELETE FROM llm_orchestrations
WHERE id=?;

-- name: delete_shares
DELETE FROM resource_group_shares
WHERE resource_type='llm_orchestration' AND resource_id=?;
