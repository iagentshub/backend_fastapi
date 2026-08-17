-- Consultas de app/services/gdpr.py.

-- name: user_exists
SELECT id
FROM users
WHERE id = ? OR username = ?;

-- name: profile
SELECT username, email, display_name, birth_date, gender, country, phone, role, created_at, preferences
FROM users
WHERE id = ?;

-- name: connections
SELECT *
FROM connections
WHERE owner_id = ?;

-- name: knowledge_items
SELECT *
FROM knowledge_items
WHERE owner_id = ?;

-- name: conversations
SELECT *
FROM conversations
WHERE user_id = ?
ORDER BY updated_at DESC;

-- name: messages_of_conversation
SELECT *
FROM messages
WHERE conversation_id = ?
ORDER BY created_at ASC;

-- name: token_daily
SELECT day, tokens
FROM token_daily
WHERE owner_id = ?
ORDER BY day DESC;

-- name: groups_of_user
SELECT w.id, w.name, w.created_at, wm.role, wm.joined_at
FROM groups w
JOIN group_members wm ON w.id = wm.group_id
WHERE wm.username = ?;

-- name: accounts
SELECT provider, linked_at
FROM accounts
WHERE owner_id = ?;

-- Los recursos salen de sus tablas, no de disco: agentes y skills viven en la
-- base de datos desde la migración a ResourceStorage, y leerlos del antiguo
-- AGENTS_DIR devolvía una lista vacía sin error ninguno.

-- name: agents
SELECT *
FROM agents
WHERE owner_id = ?
ORDER BY created_at;

-- name: skills
SELECT *
FROM skills
WHERE owner_id = ?
ORDER BY created_at;

-- name: prompts
SELECT *
FROM prompts
WHERE owner_id = ?
ORDER BY created_at;

-- name: tools
SELECT *
FROM tools
WHERE owner_id = ?
ORDER BY created_at;

-- name: workflows
SELECT *
FROM agent_workflows
WHERE owner_id = ?
ORDER BY created_at;

-- name: knowledge_packs
SELECT *
FROM knowledge_packs
WHERE owner_id = ?
ORDER BY created_at;

-- name: memory_files
SELECT *
FROM memory_files
WHERE owner_id = ?
ORDER BY updated_at;

-- name: stars
SELECT resource_type, resource_id, created_at
FROM resource_stars
WHERE username = ?
ORDER BY created_at;

-- name: follows
SELECT follower, following, created_at
FROM user_follows
WHERE follower = ? OR following = ?
ORDER BY created_at;

-- Sin los hashes del refresh: son credenciales vivas, no datos que exportar.
-- La IP y el user-agent sí van, que es lo que hace la fila un dato personal.
-- name: sessions
SELECT id, created_at, last_seen_at, expires_at, revoked_at, revoked_reason, ip, user_agent
FROM sessions
WHERE user_id = ?
ORDER BY created_at DESC;
