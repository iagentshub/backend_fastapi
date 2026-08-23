-- Consultas de app/storage/prompt_storage.py.

-- name: upsert_pg
-- engine: pg
INSERT INTO prompts (id, owner_id, name, alias, scope, data, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=EXCLUDED.name, alias=EXCLUDED.alias, scope=EXCLUDED.scope, data=EXCLUDED.data, content=EXCLUDED.content, updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
INSERT INTO prompts (id, owner_id, name, alias, scope, data, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=excluded.name, alias=excluded.alias, scope=excluded.scope, data=excluded.data, content=excluded.content, updated_at=excluded.updated_at;

-- name: list_public
SELECT id, owner_id, name, alias, scope, data, content, is_active, deactivated_at, created_at, updated_at
FROM prompts
WHERE scope='public'
ORDER BY created_at ASC;

-- name: list_private_by_owner
SELECT id, owner_id, name, alias, scope, data, content, is_active, deactivated_at, created_at, updated_at
FROM prompts
WHERE scope='private' AND owner_id=?
ORDER BY created_at ASC;

-- name: list_private
SELECT id, owner_id, name, alias, scope, data, content, is_active, deactivated_at, created_at, updated_at
FROM prompts
WHERE scope='private'
ORDER BY created_at ASC;

-- name: list_all
SELECT id, owner_id, name, alias, scope, data, content, is_active, deactivated_at, created_at, updated_at
FROM prompts
ORDER BY created_at ASC;

-- name: get_by_alias_owned
SELECT id, owner_id, name, alias, scope, data, content, is_active, deactivated_at, created_at, updated_at
FROM prompts
WHERE alias=? AND owner_id=?
LIMIT 1;

-- name: get_by_alias_public
SELECT id, owner_id, name, alias, scope, data, content, is_active, deactivated_at, created_at, updated_at
FROM prompts
WHERE alias=? AND scope='public'
LIMIT 1;

-- name: alias_taken
SELECT 1
FROM prompts
WHERE owner_id=? AND alias=? AND id != ?;

-- name: exists_scoped_owned
SELECT id
FROM prompts
WHERE id=? AND scope=? AND owner_id=?
LIMIT 1;

-- name: delete_scoped_owned
DELETE FROM prompts
WHERE id=? AND scope=? AND owner_id=?;

-- name: exists_scoped
SELECT id
FROM prompts
WHERE id=? AND scope=?
LIMIT 1;

-- name: delete_scoped
DELETE FROM prompts
WHERE id=? AND scope=?;
