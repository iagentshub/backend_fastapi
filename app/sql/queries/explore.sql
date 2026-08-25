-- Consultas de app/api/routes/explore.py.

-- name: social_card
SELECT name, description, owner, category, labels
FROM resource_social
WHERE resource_type=? AND resource_id=? AND is_public=?;

-- name: social_name_desc
SELECT name, description
FROM resource_social
WHERE resource_type=? AND resource_id=? AND is_public=?
ORDER BY updated_at DESC
LIMIT 1;

-- name: user_id_by_username
-- Sin invitados: resuelve el username de un perfil que se va a seguir, y no se
-- sigue a una cuenta que se borra al cerrar su sesión.
--
-- `LOWER()` en los dos lados porque este username llega de la URL, donde
-- cualquiera puede teclearlo con mayúsculas. Antes, `/u/Andres` resolvía en
-- `/api/users/{u}` —que normaliza en Python— y daba 404 aquí: media pantalla
-- cargada y media rota, para un perfil que existe.
SELECT id
FROM users
WHERE LOWER(username) = LOWER(?) AND role <> 'guest';

-- name: follow_insert_pg
INSERT INTO user_follows (follower, following)
VALUES (?, ?)
ON CONFLICT DO NOTHING;

-- name: follow_insert_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO user_follows (follower, following)
VALUES (?, ?);

-- name: unfollow
DELETE FROM user_follows
WHERE follower = ? AND following = ?;

-- name: is_following
SELECT 1
FROM user_follows
WHERE follower = ? AND following = ?;

-- name: count_followers
SELECT COUNT(*)
FROM user_follows
WHERE following = ?;

-- name: count_following
SELECT COUNT(*)
FROM user_follows
WHERE follower = ?;
