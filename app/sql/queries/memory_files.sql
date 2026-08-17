-- Consultas de app/storage/memory_storage.py.

-- name: count_all
SELECT COUNT(*)
FROM memory_files;

-- Inserta si no existe, sin pisar lo que ya hay. La migración legacy lo usaba
-- solo en su forma de SQLite y sin mirar el motor: en PostgreSQL era un error
-- de sintaxis que el except de al lado degradaba a un warning por fichero, así
-- que la migración no migraba nada y no lo decía.

-- name: insert_ignore_pg
INSERT INTO memory_files (id, owner_id, content, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO NOTHING;

-- name: insert_ignore_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO memory_files (id, owner_id, content, updated_at)
VALUES (?, ?, ?, ?);

-- name: list_by_owner
SELECT id, content, updated_at
FROM memory_files
WHERE owner_id=?
ORDER BY updated_at DESC;

-- name: content_of
SELECT content
FROM memory_files
WHERE id=? AND owner_id=?;

-- name: upsert_pg
-- engine: pg
INSERT INTO memory_files (id, owner_id, content, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET content=EXCLUDED.content, updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
-- engine: sqlite
INSERT OR REPLACE INTO memory_files (id, owner_id, content, updated_at)
VALUES (?, ?, ?, ?);

-- name: exists
SELECT id
FROM memory_files
WHERE id=? AND owner_id=?;

-- name: delete
DELETE FROM memory_files
WHERE id=? AND owner_id=?;
