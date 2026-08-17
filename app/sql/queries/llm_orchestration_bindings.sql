-- Consultas de app/storage/llm_orchestration_bindings.py.

-- name: get_binding
SELECT *
FROM llm_orchestration_bindings
WHERE orchestration_id=? AND user_id=?;

-- name: upsert_binding
INSERT INTO llm_orchestration_bindings (orchestration_id, user_id, definition, created_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(orchestration_id, user_id) DO
UPDATE
SET definition=excluded.definition, updated_at=excluded.updated_at;

-- name: delete_by_orchestration
DELETE FROM llm_orchestration_bindings
WHERE orchestration_id=?;
