-- Consultas de app/api/routes/admin/resources.py.

-- name: user_ids
SELECT id, username
FROM users;

-- name: list_connections
SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, created_at
FROM connections
ORDER BY created_at DESC;

-- name: connection_tokens
SELECT id, owner_id, tokens_in, tokens_out
FROM connections;

-- name: list_memory_files
-- LENGTH(content) y no content: el panel solo pinta el tamaño, así que traer el
-- texto entero de la memoria de largo plazo de cada agente de cada usuario --sin
-- cota-- para hacerle len() y tirarlo movía toda esa columna por el cable. Es la
-- misma lección que dejó escrita la mudanza del avatar fuera de `users`.
SELECT id, owner_id, LENGTH(content) AS size, updated_at
FROM memory_files
ORDER BY updated_at DESC, owner_id, id;

-- name: list_groups
SELECT g.id, g.name, g.created_by, u.username, g.created_at, g.is_active
FROM groups g
LEFT JOIN users u ON u.id = g.created_by
ORDER BY g.created_at DESC;

-- name: members_per_group
SELECT group_id, COUNT(*)
FROM group_members
GROUP BY group_id;

-- name: connections_per_owner
SELECT owner_id, COUNT(*), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0)
FROM connections
GROUP BY owner_id;

-- name: knowledge_per_owner
SELECT owner_id, COUNT(*)
FROM knowledge_items
GROUP BY owner_id;

-- name: social_exists
SELECT 1
FROM resource_social
WHERE resource_type=? AND resource_id=?;

-- name: set_verified
UPDATE resource_social
SET verified=?
WHERE resource_type=? AND resource_id=?;

-- name: user_by_username
SELECT id, username, is_active
FROM users
WHERE username=?;
