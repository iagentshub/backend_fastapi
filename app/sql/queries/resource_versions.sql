-- Consultas de app/storage/resource_versions.py.

-- name: max_version
SELECT MAX(version)
FROM resource_versions
WHERE resource_type=? AND resource_id=? AND owner_id=?;

-- name: insert_version
INSERT INTO resource_versions (id, resource_type, resource_id, owner_id, version, snapshot, created_by, reason, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: list_versions
SELECT id, version, created_by, reason, created_at
FROM resource_versions
WHERE resource_type=? AND resource_id=? AND owner_id=?
ORDER BY version DESC;

-- name: get_version
SELECT *
FROM resource_versions
WHERE resource_type=? AND resource_id=? AND owner_id=? AND version=?;
