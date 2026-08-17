-- Consultas de app/auth/github_user_link.py.

-- name: by_provider_sub
SELECT *
FROM users
WHERE provider = ? AND provider_sub = ?;

-- name: username_exists
SELECT 1
FROM users
WHERE username = ?;

-- name: email_exists
SELECT 1
FROM users
WHERE email = ?;

-- name: insert_user
INSERT INTO users (id, username, email, password_hash, display_name, provider, provider_sub, role, is_active, is_verified, created_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?);
