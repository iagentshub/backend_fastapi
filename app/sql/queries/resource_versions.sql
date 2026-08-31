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

-- name: prune_versions
-- Poda al archivar: se van las anteriores a la ventana que se conserva. El
-- borrado arrastra en cascada `tool_version_artifacts`, que declara
-- ON DELETE CASCADE sobre esta tabla.
DELETE FROM resource_versions
WHERE resource_type=? AND resource_id=? AND owner_id=? AND version<=?
RETURNING id;

-- name: get_version
SELECT *
FROM resource_versions
WHERE resource_type=? AND resource_id=? AND owner_id=? AND version=?;
