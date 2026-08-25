-- Consultas de app/api/routes/users.py.
--
-- Todas excluyen a los invitados. Desde que el invitado es un usuario efímero
-- tiene fila en `users` como cualquiera, y sin el filtro aparecería en el
-- buscador de personas, en el listado y con perfil público propio: una cuenta
-- que nadie puede seguir porque desaparece al cerrar su sesión.

-- name: public_profile
-- Sin la foto: vive en `user_avatars` y se resuelve aparte, con su checksum.
-- Aquí llegó a haber un `CASE WHEN avatar = ''` que en PostgreSQL obligaba a
-- traer la imagen entera de su almacenamiento externo solo para ver si estaba.
SELECT id, bio, languages, email, is_email_public, github, cv, created_at
FROM users
WHERE LOWER(username) = LOWER(?) AND role <> 'guest';

-- name: count_followers
SELECT COUNT(*)
FROM user_follows
WHERE following = ?;

-- name: count_following
SELECT COUNT(*)
FROM user_follows
WHERE follower = ?;

-- name: count_matching
SELECT COUNT(*)
FROM users u
WHERE u.id != ? AND u.role <> 'guest' AND LOWER(u.username) LIKE LOWER(?);

-- name: count_all
SELECT COUNT(*)
FROM users u
WHERE u.id != ? AND u.role <> 'guest';

-- name: search_page
SELECT u.username, (
SELECT COUNT(*)
FROM user_avatars
WHERE owner_id = u.id) AS has_avatar, (
SELECT COUNT(*)
FROM user_follows
WHERE following = u.id) AS followers_count, (
SELECT COUNT(*)
FROM resource_social
WHERE owner IN (u.id, u.username) AND is_public = 1) AS public_resources_count
FROM users u
WHERE u.id != ? AND u.role <> 'guest' AND LOWER(u.username) LIKE LOWER(?)
ORDER BY u.username
LIMIT ? OFFSET ?;

-- name: list_page
SELECT u.username, (
SELECT COUNT(*)
FROM user_avatars
WHERE owner_id = u.id) AS has_avatar, (
SELECT COUNT(*)
FROM user_follows
WHERE following = u.id) AS followers_count, (
SELECT COUNT(*)
FROM resource_social
WHERE owner IN (u.id, u.username) AND is_public = 1) AS public_resources_count
FROM users u
WHERE u.id != ? AND u.role <> 'guest'
ORDER BY u.username
LIMIT ? OFFSET ?;
