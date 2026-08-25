-- Consultas de app/api/routes/auth/login.py.

-- name: update_profile
UPDATE users
SET bio=?, languages=?, is_email_public=?, github=?, cv=?
WHERE id=?;

-- name: update_avatar
UPDATE users
SET avatar=?
WHERE id=?;

-- name: has_avatar
-- Solo el booleano: la columna guarda el fichero en base64 —megabytes— y
-- `/api/auth/me` está en la carga de arranque de la app. Traerla para
-- comprobar si está vacía era justo lo que evita `_USER_COLS`.
SELECT CASE WHEN avatar IS NULL OR avatar = '' THEN 0 ELSE 1 END AS has_avatar
FROM users
WHERE id=?;

-- name: clear_avatar
UPDATE users
SET avatar=NULL
WHERE id=?;
