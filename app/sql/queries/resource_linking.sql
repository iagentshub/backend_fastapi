-- Consultas de app/api/routes/resource_linking.py.

-- name: link_social_pg
INSERT INTO resource_social (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, linked_to_user, linked_to_id)
VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?)
ON CONFLICT DO NOTHING;

-- name: link_social_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO resource_social (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, linked_to_user, linked_to_id)
VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?);

-- name: link_social_tags_pg
INSERT INTO resource_social (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, linked_to_user, linked_to_id, tags)
VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)
ON CONFLICT DO NOTHING;

-- name: link_social_tags_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO resource_social (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, linked_to_user, linked_to_id, tags)
VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?);

-- name: linked_ref
SELECT linked_to_id, linked_to_user
FROM resource_social
WHERE resource_type=? AND resource_id=?;

-- name: trial_missing_deps
SELECT trial_missing_deps
FROM resource_social
WHERE resource_type=? AND resource_id=? AND is_public=?;
