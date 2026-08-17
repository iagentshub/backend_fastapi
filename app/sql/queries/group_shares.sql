-- Consultas de app/storage/group_shares.py.

-- name: cascade_shares_of_group
SELECT resource_type, resource_id
FROM resource_group_shares
WHERE group_id = ? AND via_cascade = 1;

-- name: delete_share
DELETE FROM resource_group_shares
WHERE resource_type = ? AND resource_id = ? AND group_id = ?
RETURNING group_id;

-- name: resource_ids_shared
SELECT resource_id
FROM resource_group_shares
WHERE group_id = ? AND resource_type = ?;

-- name: shares_visible_to_user
SELECT s.resource_id, s.group_id
FROM resource_group_shares s
JOIN group_members m ON m.group_id = s.group_id
JOIN groups g ON g.id = s.group_id
WHERE m.username = ? AND s.resource_type = ? AND g.is_active = 1;
