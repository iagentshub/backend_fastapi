-- Consultas de app/storage/chat.py.

-- name: insert_conversation
INSERT INTO conversations (id, user_id, agent_id, title, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?);

-- name: get_conversation
SELECT id, user_id, agent_id, title, created_at, updated_at
FROM conversations
WHERE id = ? AND user_id = ?;

-- name: touch_conversation_with_title
UPDATE conversations
SET updated_at = ?, title = CASE WHEN title = '' THEN ? ELSE title END
WHERE id = ?;

-- name: touch_conversation
UPDATE conversations
SET updated_at = ?
WHERE id = ?;

-- name: conversation_exists
SELECT id
FROM conversations
WHERE id = ? AND user_id = ?;

-- name: delete_conversation
DELETE FROM conversations
WHERE id = ? AND user_id = ?;

-- name: insert_message
INSERT INTO messages (id, conversation_id, role, content, tokens_in, tokens_out, interrupted, usage_estimated, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: recent_context
SELECT id, role, content, interrupted, usage_estimated, created_at
FROM (
SELECT m.id, m.role, SUBSTR(m.content, 1, ?) AS content, m.interrupted, m.usage_estimated, m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.user_id = ? AND c.agent_id = ? AND c.id != ?
ORDER BY m.created_at DESC, m.id DESC
LIMIT ?) recent
ORDER BY created_at ASC, id ASC;
