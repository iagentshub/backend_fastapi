-- Consultas de app/services/official_pack_service.py.

-- name: source_links_copied_by_user
SELECT DISTINCT l.source_id,l.component_key
FROM resource_source_links l
JOIN resource_social copied ON copied.resource_type=l.resource_type AND copied.linked_to_id=l.resource_id AND copied.linked_to_user=l.resource_owner_id
WHERE copied.owner=?;

-- name: components_copied_from_source
SELECT l.component_key,copied.resource_type,copied.linked_to_id,copied.linked_to_user
FROM resource_source_links l
JOIN resource_social copied ON copied.resource_type=l.resource_type AND copied.linked_to_id=l.resource_id AND copied.linked_to_user=l.resource_owner_id
WHERE l.source_id=? AND copied.owner=?;

-- name: linked_resources_of_user
SELECT resource_type,resource_id,linked_to_id,linked_to_user
FROM resource_social
WHERE owner=? AND linked_to_id IS NOT NULL;

-- name: prompt_aliases_of_owner
SELECT alias
FROM prompts
WHERE owner_id=?;
