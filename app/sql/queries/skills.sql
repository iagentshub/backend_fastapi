-- Consultas de app/storage/skill_storage.py.

-- name: count_all
SELECT COUNT(*)
FROM skills;

-- name: upsert_pg
-- engine: pg
INSERT INTO skills (id, owner_id, name, category, scope, data, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=EXCLUDED.name, category=EXCLUDED.category, scope=EXCLUDED.scope, data=EXCLUDED.data, content=EXCLUDED.content, updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
INSERT INTO skills (id, owner_id, name, category, scope, data, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=excluded.name, category=excluded.category, scope=excluded.scope, data=excluded.data, content=excluded.content, updated_at=excluded.updated_at;

-- name: list_public
SELECT id, owner_id, name, category, scope, data, content, created_at, updated_at
FROM skills
WHERE scope='public'
ORDER BY created_at ASC;

-- name: list_private_by_owner
SELECT id, owner_id, name, category, scope, data, content, created_at, updated_at
FROM skills
WHERE scope='private' AND owner_id=?
ORDER BY created_at ASC;

-- name: list_private
SELECT id, owner_id, name, category, scope, data, content, created_at, updated_at
FROM skills
WHERE scope='private'
ORDER BY created_at ASC;

-- name: list_all
SELECT id, owner_id, name, category, scope, data, content, created_at, updated_at
FROM skills
ORDER BY created_at ASC;

-- name: exists_scoped_owned
SELECT id
FROM skills
WHERE id=? AND scope=? AND owner_id=?
LIMIT 1;

-- name: delete_scoped_owned
DELETE FROM skills
WHERE id=? AND scope=? AND owner_id=?;

-- name: exists_scoped
SELECT id
FROM skills
WHERE id=? AND scope=?
LIMIT 1;

-- name: delete_scoped
DELETE FROM skills
WHERE id=? AND scope=?;
