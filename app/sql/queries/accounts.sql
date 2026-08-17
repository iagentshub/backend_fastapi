-- Consultas de app/storage/accounts.py.

-- name: count_all
SELECT COUNT(*)
FROM accounts;

-- name: upsert_pg
-- engine: pg
INSERT INTO accounts (id, owner_id, provider, data, linked_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET provider=EXCLUDED.provider, data=EXCLUDED.data, linked_at=EXCLUDED.linked_at;

-- name: upsert_sqlite
-- engine: sqlite
INSERT OR REPLACE INTO accounts (id, owner_id, provider, data, linked_at)
VALUES (?, ?, ?, ?, ?);

-- name: data_of
SELECT data
FROM accounts
WHERE owner_id = ? AND id = ?;

-- name: exists
SELECT id
FROM accounts
WHERE owner_id = ? AND id = ?;

-- name: delete
DELETE FROM accounts
WHERE owner_id = ? AND id = ?;

-- name: list_data_by_owner
SELECT data
FROM accounts
WHERE owner_id = ?
ORDER BY provider, linked_at;
