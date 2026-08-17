-- Consultas de app/storage/connection_storage.py.

-- name: count_all
SELECT COUNT(*)
FROM connections;

-- name: upsert_pg
-- engine: pg
INSERT INTO connections (id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, deactivated_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO
UPDATE
SET owner_id=EXCLUDED.owner_id, provider_account_id=EXCLUDED.provider_account_id, name=EXCLUDED.name, data=EXCLUDED.data, tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, is_active=EXCLUDED.is_active, deactivated_at=EXCLUDED.deactivated_at, updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
-- engine: sqlite
INSERT OR REPLACE INTO connections (id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, deactivated_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: list_all
SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, deactivated_at, created_at, updated_at
FROM connections
ORDER BY created_at ASC;

-- name: list_by_owner
SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, deactivated_at, created_at, updated_at
FROM connections
WHERE owner_id = ?
ORDER BY created_at ASC;

-- name: get_by_id
SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, deactivated_at, created_at, updated_at
FROM connections
WHERE id = ?;

-- name: get_owned
SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, deactivated_at, created_at, updated_at
FROM connections
WHERE id = ? AND owner_id = ?;

-- name: owner_of
SELECT owner_id
FROM connections
WHERE id = ?;

-- name: data_of_owned
SELECT data
FROM connections
WHERE id = ? AND owner_id = ?;

-- name: exists_any
SELECT id
FROM connections
WHERE id = ?;

-- name: exists_owned
SELECT id
FROM connections
WHERE id = ? AND owner_id = ?;

-- name: delete_any
DELETE FROM connections
WHERE id = ?;

-- name: delete_owned
DELETE FROM connections
WHERE id = ? AND owner_id = ?;

-- name: add_tokens
UPDATE connections
SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?
WHERE id = ?;

-- name: token_daily_pg
-- engine: pg
INSERT INTO token_daily (day, owner_id, tokens)
VALUES (?, ?, ?)
ON CONFLICT (day, owner_id) DO
UPDATE
SET tokens = token_daily.tokens + EXCLUDED.tokens;

-- name: token_daily_sqlite
INSERT INTO token_daily (day, owner_id, tokens)
VALUES (?, ?, ?)
ON CONFLICT(day, owner_id) DO
UPDATE
SET tokens = token_daily.tokens + excluded.tokens;

-- name: tokens_per_day_of_owner
SELECT day, SUM(tokens)
FROM token_daily
WHERE owner_id = ? AND day >= ?
GROUP BY day
ORDER BY day ASC;

-- name: seed_token_daily_pg
INSERT INTO token_daily (day, owner_id, tokens)
SELECT ?, owner_id, tokens_in + tokens_out
FROM connections
WHERE owner_id = ? AND tokens_in + tokens_out > 0
ON CONFLICT (day, owner_id) DO NOTHING;

-- name: seed_token_daily_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO token_daily (day, owner_id, tokens)
SELECT ?, owner_id, tokens_in + tokens_out
FROM connections
WHERE owner_id = ? AND tokens_in + tokens_out > 0;
