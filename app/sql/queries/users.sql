-- Consultas de app/api/routes/users.py.

-- name: public_profile
SELECT id, CASE WHEN avatar IS NULL OR avatar = '' THEN 0 ELSE 1 END, bio, languages, email, is_email_public, github, cv, created_at
FROM users
WHERE username = ?;

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
WHERE u.id != ? AND LOWER(u.username) LIKE LOWER(?);

-- name: count_all
SELECT COUNT(*)
FROM users u
WHERE u.id != ?;

-- name: search_page
SELECT u.username, CASE WHEN u.avatar IS NULL OR u.avatar = '' THEN 0 ELSE 1 END, (
SELECT COUNT(*)
FROM user_follows
WHERE following = u.id) AS followers_count, (
SELECT COUNT(*)
FROM resource_social
WHERE owner IN (u.id, u.username) AND is_public = 1) AS public_resources_count
FROM users u
WHERE u.id != ? AND LOWER(u.username) LIKE LOWER(?)
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
WHERE u.id != ?
ORDER BY u.username
LIMIT ? OFFSET ?;

-- name: avatar_of
SELECT avatar
FROM users
WHERE username=?;
