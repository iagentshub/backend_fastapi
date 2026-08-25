-- Consultas de app/auth/auth.py.

-- name: username_exists
SELECT 1
FROM users
WHERE username = ?;

-- name: email_exists
SELECT 1
FROM users
WHERE email = ?;

-- name: insert_user_basic
INSERT INTO users (id, username, email, password_hash, role, is_active, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?);

-- name: insert_user_full
INSERT INTO users (id, username, email, password_hash, display_name, birth_date, gender, country, phone, role, is_active, is_verified, verification_token, created_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?);

-- name: username_by_verification_token
SELECT username
FROM users
WHERE verification_token = ? AND is_verified = 0;

-- name: mark_verified
UPDATE users
SET is_verified = 1, verification_token = NULL
WHERE username = ?;

-- name: username_by_email_active
SELECT username
FROM users
WHERE email = ? AND is_active = 1;

-- name: set_reset_token
UPDATE users
SET reset_token = ?, reset_token_expires = ?
WHERE email = ?;

-- name: reset_token_expiry
SELECT reset_token_expires
FROM users
WHERE reset_token = ?;

-- name: reset_password_by_token
UPDATE users
SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL
WHERE reset_token = ? AND reset_token_expires > ?
RETURNING username, email;

-- name: set_password_changed_at
UPDATE users
SET password_changed_at = ?
WHERE id = ? OR username = ?;

-- name: email_matches_user
SELECT 1
FROM users
WHERE (id = ? OR username = ?) AND lower(email) = ?;

-- name: set_password_by_id_or_username
UPDATE users
SET password_hash = ?
WHERE id = ? OR username = ?;

-- name: list_users
-- Sin los invitados: son usuarios efímeros que se borran solos, y en el panel
-- de administración serían ruido que aparece y desaparece entre dos recargas.
--
-- El `SELECT *` fue peligroso mientras la foto era una columna de `users`: el
-- panel se traía el base64 de **todos** los usuarios en cada carga. Desde que
-- vive en `user_avatars` solo viaja su checksum, que es lo que arma la URL.
SELECT u.*, a.checksum AS avatar_checksum
FROM users u
LEFT JOIN user_avatars a ON a.owner_id = u.id
WHERE u.role <> 'guest'
ORDER BY u.created_at ASC;

-- name: set_password_by_username
UPDATE users
SET password_hash = ?
WHERE username = ?;

-- name: password_hash_by_email
SELECT password_hash
FROM users
WHERE email = ?;

-- name: username_role_by_email
SELECT username, role
FROM users
WHERE email = ?;

-- name: set_role_by_email
UPDATE users
SET role = ?
WHERE email = ?;

-- name: set_password_by_email
UPDATE users
SET password_hash = ?
WHERE email = ?;

-- name: first_user_with_role
SELECT username
FROM users
WHERE role = ?
LIMIT 1;

-- name: insert_user_with_role
INSERT INTO users (id, username, email, password_hash, role, is_active, is_verified, created_at)
VALUES (?,?,?,?,?,?,?,?);
