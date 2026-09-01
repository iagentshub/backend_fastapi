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

-- name: delete_knowledge_chunks
DELETE FROM knowledge_chunks
WHERE knowledge_id IN (
SELECT id FROM knowledge_items WHERE owner_id = ?);

-- name: delete_knowledge_packs
DELETE FROM knowledge_packs
WHERE owner_id = ?;

-- name: delete_prompts
DELETE FROM prompts
WHERE owner_id = ?;

-- name: delete_tools
DELETE FROM tools
WHERE owner_id = ?;

-- name: delete_tool_artifact_links
DELETE FROM tool_artifact_links
WHERE owner_id = ?;

-- name: delete_orphan_tool_artifacts
DELETE FROM tool_artifacts
WHERE NOT EXISTS (
    SELECT 1 FROM tool_artifact_links AS link
    WHERE link.sha256=tool_artifacts.sha256
)
AND NOT EXISTS (
    SELECT 1 FROM tool_version_artifacts AS version_artifact
    WHERE version_artifact.sha256=tool_artifacts.sha256
);

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

-- name: delete_resource_labels
DELETE FROM resource_labels
WHERE owner_id = ?;

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

-- name: delete_personal_access_tokens
DELETE FROM personal_access_tokens
WHERE username = ?;

-- name: delete_vscode_auth_codes
DELETE FROM vscode_auth_codes
WHERE username = ?;

-- name: delete_workflow_run_events
-- Explícito aunque exista ON DELETE CASCADE: en SQLite `foreign_keys` es un
-- PRAGMA por conexión y la purga no debe depender de que esté activado.
DELETE FROM workflow_run_events
WHERE run_id IN (
SELECT id
FROM workflow_runs
WHERE started_by = ?);

-- name: delete_resource_executions
DELETE FROM resource_executions
WHERE started_by = ?;

-- name: delete_workflow_runs
DELETE FROM workflow_runs
WHERE started_by = ?;

-- name: delete_subscription_license_assignments
-- Incluye las licencias que recibió o asignó la cuenta y todos los asientos de
-- una suscripción suya. Debe ejecutarse antes de borrar `subscriptions`.
DELETE FROM subscription_license_assignments
WHERE username = ? OR assigned_by = ? OR subscription_id IN (
SELECT id
FROM subscriptions
WHERE username = ?);

-- name: delete_subscriptions
DELETE FROM subscriptions
WHERE username = ?;

-- name: delete_group_shares
DELETE FROM resource_group_shares
WHERE shared_by = ?;

-- name: delete_push_subscriptions
DELETE FROM push_subscriptions
WHERE user_id = ?;

-- name: delete_notifications
DELETE FROM notifications
WHERE user_id = ?;

-- name: delete_group_invitations
DELETE FROM group_invitations
WHERE username = ?;

-- name: delete_group_members
DELETE FROM group_members
WHERE username = ?;

-- name: delete_groups
DELETE FROM groups
WHERE created_by = ?;

-- name: delete_agent_preferences
-- La columna se llama `username` por herencia, pero lo que guarda es el id del
-- usuario: quien escribe la fila es `require_auth`, que devuelve el id. Estaba
-- fuera de la purga —como el resto de tablas indexadas por username— y dejaba
-- atrás la conexión preferida de una cuenta ya borrada.
DELETE FROM user_agent_preferences
WHERE username = ?;

-- name: delete_sessions
DELETE FROM sessions
WHERE user_id = ?;

-- name: delete_legal_acceptances
DELETE FROM legal_acceptances
WHERE user_id = ?;

-- name: delete_user
DELETE FROM users
WHERE id = ?;

-- name: pending_deletions
SELECT username
FROM users
WHERE deletion_requested_at IS NOT NULL AND deletion_requested_at <= ?;

-- name: delete_user_avatar
DELETE FROM user_avatars
WHERE owner_id = ?;
