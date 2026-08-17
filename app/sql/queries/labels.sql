-- Consultas de app/storage/labels.py.

-- name: delete_labels
DELETE FROM resource_labels
WHERE resource_type=? AND resource_id=?;

-- name: insert_label
INSERT INTO resource_labels (resource_type, resource_id, owner_id, label)
VALUES (?, ?, ?, ?);

-- name: by_label
SELECT resource_type, resource_id, owner_id
FROM resource_labels
WHERE label=?
ORDER BY resource_type, resource_id;

-- name: by_label_and_owner
SELECT resource_type, resource_id, owner_id
FROM resource_labels
WHERE label=? AND owner_id=?
ORDER BY resource_type, resource_id;
