-- Consultas de app/storage/groups.py.

-- name: get_by_id
SELECT *
FROM groups
WHERE id = ?;

-- name: list_for_user
SELECT w.*, wm.role, u.username AS created_by_username
FROM groups w
JOIN group_members wm ON w.id = wm.group_id
LEFT JOIN users u ON u.id = w.created_by
WHERE wm.username = ?
ORDER BY w.created_at ASC;

-- name: insert_group
INSERT INTO groups (id, name, created_by, created_at)
VALUES (?, ?, ?, ?);

-- name: insert_owner_member
INSERT INTO group_members (group_id, username, role, joined_at)
VALUES (?, ?, ?, ?);

-- name: update_name
UPDATE groups
SET name = ?
WHERE id = ?
RETURNING id;

-- name: agent_ids_by_owner
SELECT id
FROM agents
WHERE owner_id = ?;

-- name: skill_ids_by_owner
SELECT id
FROM skills
WHERE owner_id = ?;

-- name: knowledge_ids_by_owner
SELECT id
FROM knowledge_items
WHERE owner_id = ?;

-- name: connection_ids_by_owner
SELECT id
FROM connections
WHERE owner_id = ?;

-- name: workflow_ids_by_owner
SELECT id
FROM agent_workflows
WHERE owner_id = ?;

-- name: delete_social_by_resource
DELETE FROM resource_social
WHERE resource_type = ? AND resource_id = ?;

-- name: delete_shares_by_resource
DELETE FROM resource_group_shares
WHERE resource_type = ? AND resource_id = ?;

-- name: delete_agents_by_owner
DELETE FROM agents
WHERE owner_id = ?;

-- name: delete_skills_by_owner
DELETE FROM skills
WHERE owner_id = ?;

-- name: delete_knowledge_by_owner
DELETE FROM knowledge_items
WHERE owner_id = ?;

-- name: delete_knowledge_chunks_by_owner
DELETE FROM knowledge_chunks
WHERE knowledge_id IN (
SELECT id FROM knowledge_items WHERE owner_id = ?);

-- name: delete_connections_by_owner
DELETE FROM connections
WHERE owner_id = ?;

-- name: delete_workflows_by_owner
DELETE FROM agent_workflows
WHERE owner_id = ?;

-- name: delete_shares_by_group
DELETE FROM resource_group_shares
WHERE group_id = ?;

-- name: delete_invitations_by_group
DELETE FROM group_invitations
WHERE group_id = ?;

-- name: delete_members_by_group
DELETE FROM group_members
WHERE group_id = ?;

-- name: delete_group
DELETE FROM groups
WHERE id = ?
RETURNING id;

-- name: set_active
UPDATE groups
SET is_active = ?
WHERE id = ?
RETURNING id;

-- name: member_exists
SELECT 1
FROM group_members
WHERE group_id = ? AND username = ?;

-- name: set_created_by
UPDATE groups
SET created_by = ?
WHERE id = ?;

-- name: set_member_role
UPDATE group_members
SET role = ?
WHERE group_id = ? AND username = ?;

-- name: list_members
-- El checksum del avatar, no la imagen: con él se arma la URL versionada y la
-- lista no arrastra un fichero por miembro.
SELECT u.username, wm.role, wm.permissions, wm.joined_at, u.display_name,
       a.checksum
FROM group_members wm
JOIN users u ON u.id = wm.username
LEFT JOIN user_avatars a ON a.owner_id = u.id
WHERE wm.group_id = ?
ORDER BY wm.joined_at ASC;

-- name: get_member
SELECT *
FROM group_members
WHERE group_id = ? AND username = ?;

-- name: upsert_member_pg
INSERT INTO group_members (group_id, username, role, joined_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (group_id, username) DO
UPDATE
SET role = ?;

-- name: upsert_member_sqlite
INSERT INTO group_members (group_id, username, role, joined_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(group_id, username) DO
UPDATE
SET role=excluded.role;

-- name: delete_member
DELETE FROM group_members
WHERE group_id = ? AND username = ?
RETURNING group_id;

-- name: update_member_role
UPDATE group_members
SET role = ?
WHERE group_id = ? AND username = ?
RETURNING group_id;

-- name: update_member_permissions
UPDATE group_members
SET permissions = ?
WHERE group_id = ? AND username = ?
RETURNING group_id;

-- name: insert_invitation_pg
INSERT INTO group_invitations (id, group_id, invited_by, username, status, created_at)
VALUES (?, ?, ?, ?, 'pending', ?)
ON CONFLICT (group_id, username) DO NOTHING
RETURNING id;

-- name: insert_invitation_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO group_invitations (id, group_id, invited_by, username, status, created_at)
VALUES (?, ?, ?, ?, 'pending', ?)
RETURNING id;

-- name: list_group_invitations
SELECT wi.id, wi.group_id, wi.invited_by, u.username, wi.status, wi.created_at
FROM group_invitations wi
JOIN users u ON u.id = wi.username
WHERE wi.group_id = ? AND wi.status = 'pending'
ORDER BY wi.created_at DESC;

-- name: list_user_invitations
SELECT wi.id, wi.group_id, wi.invited_by, u.username, wi.status, wi.created_at, w.name AS group_name
FROM group_invitations wi
LEFT JOIN groups w ON w.id = wi.group_id
JOIN users u ON u.id = wi.username
WHERE wi.username = ? AND wi.status = 'pending'
ORDER BY wi.created_at DESC;

-- name: delete_invitation_by_group
DELETE FROM group_invitations
WHERE id = ? AND group_id = ?
RETURNING id;

-- name: get_pending_invitation
SELECT *
FROM group_invitations
WHERE id = ? AND username = ? AND status = 'pending';

-- name: add_member_ignore_pg
INSERT INTO group_members (group_id, username, role, joined_at)
VALUES (?, ?, 'member', ?)
ON CONFLICT (group_id, username) DO NOTHING;

-- name: add_member_ignore_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO group_members (group_id, username, role, joined_at)
VALUES (?, ?, 'member', ?);

-- name: delete_invitation
DELETE FROM group_invitations
WHERE id = ?;

-- name: delete_invitation_by_user
DELETE FROM group_invitations
WHERE id = ? AND username = ?
RETURNING id;
