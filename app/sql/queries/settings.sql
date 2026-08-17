-- Consultas de app/api/routes/settings.py.

-- name: preferences_of_user
SELECT preferences
FROM users
WHERE id = ?;

-- name: set_preferences
UPDATE users
SET preferences = ?
WHERE id = ?;
