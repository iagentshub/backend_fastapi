-- Consultas de app/api/routes/sharing.py.

-- name: groups_of_resource
SELECT group_id
FROM resource_group_shares
WHERE resource_type = ? AND resource_id = ?;
