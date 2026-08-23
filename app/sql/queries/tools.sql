-- Consultas de app/storage/tool_storage.py.

-- name: upsert_pg
-- engine: pg
INSERT INTO tools (id, owner_id, name, language, scope, data, content, binary_b64, binary_filename, binary_size, binary_uploaded_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=EXCLUDED.name, language=EXCLUDED.language, scope=EXCLUDED.scope, data=EXCLUDED.data, content=EXCLUDED.content, binary_b64=EXCLUDED.binary_b64, binary_filename=EXCLUDED.binary_filename, binary_size=EXCLUDED.binary_size, binary_uploaded_at=EXCLUDED.binary_uploaded_at, updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
INSERT INTO tools (id, owner_id, name, language, scope, data, content, binary_b64, binary_filename, binary_size, binary_uploaded_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=excluded.name, language=excluded.language, scope=excluded.scope, data=excluded.data, content=excluded.content, binary_b64=excluded.binary_b64, binary_filename=excluded.binary_filename, binary_size=excluded.binary_size, binary_uploaded_at=excluded.binary_uploaded_at, updated_at=excluded.updated_at;

-- name: list_public
SELECT id, owner_id, name, language, scope, data, content, binary_b64, binary_filename, binary_size, binary_uploaded_at, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE scope='public'
ORDER BY created_at ASC;

-- name: list_private_by_owner
SELECT id, owner_id, name, language, scope, data, content, binary_b64, binary_filename, binary_size, binary_uploaded_at, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE scope='private' AND owner_id=?
ORDER BY created_at ASC;

-- name: list_private
SELECT id, owner_id, name, language, scope, data, content, binary_b64, binary_filename, binary_size, binary_uploaded_at, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE scope='private'
ORDER BY created_at ASC;

-- name: list_all
SELECT id, owner_id, name, language, scope, data, content, binary_b64, binary_filename, binary_size, binary_uploaded_at, is_active, deactivated_at, created_at, updated_at
FROM tools
ORDER BY created_at ASC;

-- name: exists_scoped_owned
SELECT id
FROM tools
WHERE id=? AND scope=? AND owner_id=?
LIMIT 1;

-- name: delete_scoped_owned
DELETE FROM tools
WHERE id=? AND scope=? AND owner_id=?;

-- name: exists_scoped
SELECT id
FROM tools
WHERE id=? AND scope=?
LIMIT 1;

-- name: delete_scoped
DELETE FROM tools
WHERE id=? AND scope=?;

-- name: exists_owned
SELECT id
FROM tools
WHERE id=? AND owner_id=?
LIMIT 1;

-- name: set_binary_owned
UPDATE tools
SET binary_b64=?, binary_filename=?, binary_size=?, binary_uploaded_at=?, updated_at=?
WHERE id=? AND owner_id=?;

-- name: exists_any
SELECT id
FROM tools
WHERE id=?
LIMIT 1;

-- name: set_binary
UPDATE tools
SET binary_b64=?, binary_filename=?, binary_size=?, binary_uploaded_at=?, updated_at=?
WHERE id=?;
