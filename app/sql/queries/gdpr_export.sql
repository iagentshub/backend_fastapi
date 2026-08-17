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
