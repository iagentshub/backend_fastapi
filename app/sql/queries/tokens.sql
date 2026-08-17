-- Consultas de app/storage/tokens.py.

-- name: purge_expired_codes
DELETE FROM vscode_auth_codes
WHERE expires_at <= ?;

-- name: insert_auth_code
INSERT INTO vscode_auth_codes (code_hash, username, state, expires_at)
VALUES (?, ?, ?, ?);

-- name: get_auth_code
SELECT *
FROM vscode_auth_codes
WHERE code_hash = ?;

-- name: delete_auth_code
DELETE FROM vscode_auth_codes
WHERE code_hash = ?;

-- name: insert_pat
INSERT INTO personal_access_tokens (id, username, name, token_hash, prefix, created_at, expires_at)
VALUES (?, ?, ?, ?, ?, ?, ?);

-- name: list_pats
SELECT *
FROM personal_access_tokens
WHERE username = ?
ORDER BY created_at DESC;

-- name: active_pat_of_user
SELECT id
FROM personal_access_tokens
WHERE id = ? AND username = ? AND revoked_at IS NULL;

-- name: revoke_pat
UPDATE personal_access_tokens
SET revoked_at = ?
WHERE id = ?;

-- name: pat_by_hash
SELECT *
FROM personal_access_tokens
WHERE token_hash = ?;

-- name: touch_pat
UPDATE personal_access_tokens
SET last_used_at = ?
WHERE id = ?;
