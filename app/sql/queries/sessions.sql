-- Consultas de app/storage/sessions.py.

-- name: insert_session
INSERT INTO sessions (id, user_id, refresh_hash, created_at, last_seen_at, expires_at, ip, user_agent)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);

-- name: get_session
SELECT *
FROM sessions
WHERE id = ?;

-- name: session_by_refresh
SELECT *
FROM sessions
WHERE refresh_hash = ?;

-- name: session_by_prev_refresh
SELECT *
FROM sessions
WHERE prev_refresh_hash = ?;

-- name: rotate_refresh
UPDATE sessions
SET prev_refresh_hash = refresh_hash,
    refresh_hash      = ?,
    last_seen_at      = ?,
    expires_at        = ?
WHERE id = ? AND revoked_at IS NULL;

-- name: touch_session
UPDATE sessions
SET last_seen_at = ?
WHERE id = ?;

-- name: list_sessions_of_user
SELECT *
FROM sessions
WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
ORDER BY created_at DESC;

-- name: revoke_session
UPDATE sessions
SET revoked_at = ?, revoked_reason = ?, refresh_hash = NULL, prev_refresh_hash = NULL
WHERE id = ? AND revoked_at IS NULL;

-- name: revoke_sessions_of_user
UPDATE sessions
SET revoked_at = ?, revoked_reason = ?, refresh_hash = NULL, prev_refresh_hash = NULL
WHERE user_id = ? AND revoked_at IS NULL;

-- name: revoke_other_sessions_of_user
UPDATE sessions
SET revoked_at = ?, revoked_reason = ?, refresh_hash = NULL, prev_refresh_hash = NULL
WHERE user_id = ? AND id <> ? AND revoked_at IS NULL;

-- name: active_session_of_user
SELECT id
FROM sessions
WHERE id = ? AND user_id = ? AND revoked_at IS NULL;

-- Revocación desde los caminos que solo conocen al usuario por su identidad
-- (cambio de contraseña, desactivación de cuenta): ahí el parámetro puede ser
-- el id o el nombre, mientras que `sessions.user_id` guarda siempre el id.
-- name: revoke_sessions_by_identity
UPDATE sessions
SET revoked_at = ?, revoked_reason = ?, refresh_hash = NULL, prev_refresh_hash = NULL
WHERE revoked_at IS NULL
  AND user_id IN (SELECT id FROM users WHERE id = ? OR lower(username) = ?);

-- name: purge_expired_sessions
DELETE FROM sessions
WHERE expires_at <= ?;
