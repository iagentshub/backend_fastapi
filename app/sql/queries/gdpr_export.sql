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

-- name: tool_artifacts
SELECT sha256, binary_data, size, created_at
FROM tool_artifacts AS artifact
WHERE EXISTS (
    SELECT 1
    FROM tool_artifact_links AS link
    WHERE link.sha256=artifact.sha256 AND link.owner_id=?
)
ORDER BY sha256;

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

-- La conexión que el usuario eligió para cada agente. La columna se llama
-- `username` por herencia y guarda el id: quien escribe la fila es
-- `require_auth`, que devuelve el id.
-- name: agent_preferences
SELECT agent_id, connection_id, updated_at
FROM user_agent_preferences
WHERE username = ?
ORDER BY updated_at DESC;

-- PAT: solo metadatos reconocibles por el usuario. `token_hash` es material de
-- autenticación y no forma parte del ZIP.
-- name: personal_access_tokens
SELECT id, name, prefix, created_at, expires_at, last_used_at, revoked_at
FROM personal_access_tokens
WHERE username = ?
ORDER BY created_at DESC;

-- name: subscriptions
SELECT id, stripe_customer_id, stripe_subscription_id, tier, seats, self_hosted,
       interval, amount_cents, status, current_period_end,
       cancel_at_period_end, created_at, updated_at
FROM subscriptions
WHERE username = ?
ORDER BY created_at DESC;

-- Licencias recibidas por el usuario. Las asignaciones de una suscripción que
-- posee aparecen además como parte de su metadato de facturación.
-- name: subscription_license_assignments
SELECT subscription_id, username, assigned_by, assigned_at, status
FROM subscription_license_assignments
WHERE username = ?
ORDER BY assigned_at DESC;

-- name: subscription_assignments_owned
SELECT la.subscription_id, la.username, la.assigned_by, la.assigned_at, la.status
FROM subscription_license_assignments la
JOIN subscriptions s ON s.id = la.subscription_id
WHERE s.username = ?
ORDER BY la.assigned_at DESC;

-- name: workflow_runs
SELECT *
FROM workflow_runs
WHERE started_by = ?
ORDER BY created_at DESC;

-- name: workflow_run_events
SELECT e.*
FROM workflow_run_events e
JOIN workflow_runs r ON r.id = e.run_id
WHERE r.started_by = ?
ORDER BY e.run_id, e.sequence;
