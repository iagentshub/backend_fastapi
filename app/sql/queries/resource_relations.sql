-- Consultas de app/services/resource_relations.py.
-- Todas van dirigidas al recurso pedido: el vecindario de un nodo no
-- necesita el inventario de la instalación.

-- name: knowledge_pack_of
SELECT pack_id, pack_relative_path
FROM knowledge_items
WHERE id = ? AND pack_id IS NOT NULL;

-- name: pack_members
SELECT id, title AS name, pack_relative_path, pack_kind
FROM knowledge_items
WHERE pack_id = ?
ORDER BY pack_relative_path;

-- name: workflow_agent_presentations
SELECT a.id,
       a.name AS stored_name,
       social.name AS public_name,
       social.description AS public_description,
       CASE WHEN social.resource_id IS NULL THEN 0 ELSE 1 END AS is_public
FROM agents a
LEFT JOIN resource_social social
  ON social.resource_type = 'agent'
 AND social.resource_id = a.id
 AND social.is_public = 1
WHERE a.id IN (@agent_ids@);

-- name: group_memberships_of_user
SELECT group_id, role
FROM group_members
WHERE username = ?;

-- name: members_of_group
SELECT username, role
FROM group_members
WHERE group_id = ?;

-- name: shares_of_group
SELECT resource_type, resource_id
FROM resource_group_shares
WHERE group_id = ?;

-- name: shares_of_resource
SELECT group_id
FROM resource_group_shares
WHERE resource_type = ? AND resource_id = ?;

-- name: account_by_id
SELECT id, owner_id, provider, data
FROM accounts
WHERE id = ? AND owner_id = ?;

-- name: source_link_of_resource
SELECT source_id, resource_type, resource_id, resource_owner_id
FROM resource_source_links
WHERE resource_type = ? AND resource_id = ? AND resource_owner_id = ?;


-- name: user_by_id
SELECT id, username
FROM users
WHERE id = ?;

-- name: group_by_id
SELECT id, name
FROM groups
WHERE id = ?;

-- name: source_by_id
SELECT id, name, repository_url
FROM official_sources
WHERE id = ?;

-- name: memory_by_id
SELECT id, owner_id
FROM memory_files
WHERE id = ? AND owner_id = ?;


-- name: user_by_username
SELECT id, username
FROM users
WHERE username = ?;

-- name: source_links_of_owner
SELECT l.source_id, l.resource_type, l.resource_id, s.name, s.repository_url
FROM resource_source_links l
JOIN official_sources s ON s.id = l.source_id
WHERE l.resource_owner_id = ?;

-- name: accounts_of_owner
SELECT id, provider, data
FROM accounts
WHERE owner_id = ?;
