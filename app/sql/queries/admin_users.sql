-- Consultas de app/api/routes/admin/users.py.

-- name: email_exists
SELECT 1
FROM users
WHERE email = ?;

-- name: username_exists
SELECT 1
FROM users
WHERE username = ?;

-- name: insert_user
INSERT INTO users (id, username, email, password_hash, display_name, role, is_active, is_verified, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
