-- Consultas de app/services/official_source_sync.py.

-- name: delete_source
DELETE FROM official_sources
WHERE id=?;

-- name: agents_of_owner
SELECT id,owner_id,data
FROM agents
WHERE owner_id=?;

-- name: update_agent_data
UPDATE agents
SET data=?
WHERE id=? AND owner_id=?;

-- name: delete_labels_of_resource
DELETE FROM resource_labels
WHERE resource_type=? AND resource_id=? AND owner_id=?;

-- name: delete_social_of_resource
DELETE FROM resource_social
WHERE resource_type=? AND resource_id=? AND owner=?;

-- name: delete_shares_of_resource
DELETE FROM resource_group_shares
WHERE resource_type=? AND resource_id=? AND shared_by=?;

-- name: delete_versions_of_resource
DELETE FROM resource_versions
WHERE resource_type=? AND resource_id=? AND owner_id=?;
