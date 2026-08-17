-- Consultas de app/auth/gdpr.py.

-- name: groups_created_by
SELECT id, name
FROM groups
WHERE created_by = ?;

-- name: request_deletion
UPDATE users
SET deletion_requested_at = ?, deletion_token = ?
WHERE id = ? OR username = ?;

-- name: deletion_token_exists
SELECT 1
FROM users
WHERE deletion_token = ?;

-- name: cancel_deletion
UPDATE users
SET deletion_requested_at = NULL, deletion_token = NULL
WHERE deletion_token = ?;

-- name: delete_messages
DELETE FROM messages
WHERE conversation_id IN (
SELECT id
FROM conversations
WHERE user_id = ?);

-- name: delete_conversations
DELETE FROM conversations
WHERE user_id = ?;

-- name: delete_agents
DELETE FROM agents
WHERE owner_id = ?;

-- name: delete_skills
DELETE FROM skills
WHERE owner_id = ?;

-- name: delete_knowledge_items
DELETE FROM knowledge_items
WHERE owner_id = ?;

-- name: delete_knowledge_packs
DELETE FROM knowledge_packs
WHERE owner_id = ?;

-- name: delete_prompts
DELETE FROM prompts
WHERE owner_id = ?;

-- name: delete_tools
DELETE FROM tools
WHERE owner_id = ?;

-- name: delete_memory_files
DELETE FROM memory_files
WHERE owner_id = ?;

-- name: delete_resource_versions
DELETE FROM resource_versions
WHERE owner_id = ? OR created_by = ?;

-- name: delete_resource_source_links
DELETE FROM resource_source_links
WHERE resource_owner_id = ?;

-- name: delete_connections
DELETE FROM connections
WHERE owner_id = ?;

-- name: delete_orchestration_bindings
DELETE FROM llm_orchestration_bindings
WHERE user_id = ? OR orchestration_id IN (
SELECT id
FROM llm_orchestrations
WHERE owner_id = ?);

-- name: delete_orchestrations
DELETE FROM llm_orchestrations
WHERE owner_id = ?;

-- name: delete_workflows
DELETE FROM agent_workflows
WHERE owner_id = ?;

-- name: delete_social
DELETE FROM resource_social
WHERE owner = ?;

-- name: delete_stars
DELETE FROM resource_stars
WHERE username = ?;

-- name: delete_follows
DELETE FROM user_follows
WHERE follower = ? OR following = ?;

-- name: delete_token_daily
DELETE FROM token_daily
WHERE owner_id = ?;

-- name: delete_accounts
DELETE FROM accounts
WHERE owner_id = ?;

-- name: delete_group_shares
DELETE FROM resource_group_shares
WHERE shared_by = ?;

-- name: delete_group_invitations
DELETE FROM group_invitations
WHERE username = ?;

-- name: delete_group_members
DELETE FROM group_members
WHERE username = ?;

-- name: delete_groups
DELETE FROM groups
WHERE created_by = ?;

-- name: delete_sessions
DELETE FROM sessions
WHERE user_id = ?;

-- name: delete_user
DELETE FROM users
WHERE id = ?;

-- name: pending_deletions
SELECT username
FROM users
WHERE deletion_requested_at IS NOT NULL AND deletion_requested_at <= ?;
