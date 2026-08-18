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
SELECT id
FROM users
WHERE username = ?;

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
