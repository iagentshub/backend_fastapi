-- Consultas de app/api/routes/admin/explore.py.

-- name: pack_files
SELECT pack_id,id AS knowledge_id,pack_relative_path AS relative_path,pack_kind AS kind
FROM knowledge_items
WHERE pack_id IS NOT NULL
ORDER BY pack_id,pack_relative_path;

-- name: all_accounts
SELECT id,owner_id,provider,data
FROM accounts;

-- name: all_group_members
SELECT group_id, username, role
FROM group_members;

-- name: all_group_shares
SELECT group_id, resource_type, resource_id
FROM resource_group_shares;
