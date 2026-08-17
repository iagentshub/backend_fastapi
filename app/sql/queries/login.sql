-- Consultas de app/api/routes/auth/login.py.

-- name: update_profile
UPDATE users
SET bio=?, languages=?, is_email_public=?, github=?, cv=?
WHERE id=?;

-- name: update_avatar
UPDATE users
SET avatar=?
WHERE id=?;
