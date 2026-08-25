-- Consultas de app/api/routes/users.py.
--
-- Todas excluyen a los invitados. Desde que el invitado es un usuario efímero
-- tiene fila en `users` como cualquiera, y sin el filtro aparecería en el
-- buscador de personas, en el listado y con perfil público propio: una cuenta
-- que nadie puede seguir porque desaparece al cerrar su sesión.

-- name: public_profile
SELECT id, CASE WHEN avatar IS NULL OR avatar = '' THEN 0 ELSE 1 END, bio, languages, email, is_email_public, github, cv, created_at
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
SELECT u.username, CASE WHEN u.avatar IS NULL OR u.avatar = '' THEN 0 ELSE 1 END, (
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
SELECT u.username, CASE WHEN u.avatar IS NULL OR u.avatar = '' THEN 0 ELSE 1 END, (
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

-- name: avatar_of
SELECT avatar
FROM users
WHERE LOWER(username) = LOWER(?) AND role <> 'guest';
