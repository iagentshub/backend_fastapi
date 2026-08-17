-- Consultas de app/api/routes/social.py.

-- name: public_flag_exists
SELECT 1
FROM resource_social
WHERE resource_type=? AND resource_id=? AND is_public=?;

-- name: linked_to_id
SELECT linked_to_id
FROM resource_social
WHERE resource_type=? AND resource_id=? AND owner=?;

-- name: pack_make_public
UPDATE knowledge_packs
SET scope='public',labels=?,updated_at=?
WHERE id=?;

-- name: pack_make_private
UPDATE knowledge_packs
SET scope='private',updated_at=?
WHERE id=?;

-- name: delete_social_pack_cascade
DELETE FROM resource_social
WHERE ((resource_type='knowledge_pack' AND resource_id=?) OR (resource_type='knowledge' AND resource_id IN (
SELECT id
FROM knowledge_items
WHERE pack_id=?)));

-- name: delete_social_knowledge
DELETE FROM resource_social
WHERE resource_type='knowledge' AND resource_id=?;

-- name: upsert_social_pg
-- engine: pg
INSERT INTO resource_social (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
ON CONFLICT (resource_type, resource_id, owner) DO
UPDATE
SET name=EXCLUDED.name, description=EXCLUDED.description, is_public=EXCLUDED.is_public, category=EXCLUDED.category, trial_missing_deps=EXCLUDED.trial_missing_deps, tags=EXCLUDED.tags, labels=EXCLUDED.labels, updated_at=now();

-- name: upsert_social_sqlite
-- engine: sqlite
INSERT INTO resource_social (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(resource_type, resource_id, owner) DO
UPDATE
SET name=excluded.name, description=excluded.description, is_public=excluded.is_public, category=excluded.category, trial_missing_deps=excluded.trial_missing_deps, tags=excluded.tags, labels=excluded.labels, updated_at=excluded.updated_at;

-- name: pack_publish_owned
UPDATE knowledge_packs
SET scope='public', labels=?, updated_at=?
WHERE id=? AND owner_id=?;

-- name: pack_unpublish_owned
UPDATE knowledge_packs
SET scope='private', labels=?, updated_at=?
WHERE id=? AND owner_id=?;

-- name: delete_social_entry
DELETE FROM resource_social
WHERE resource_type=? AND resource_id=? AND owner=?;

-- name: star_insert_pg
INSERT INTO resource_stars (username, resource_type, resource_id)
VALUES (?, ?, ?)
ON CONFLICT DO NOTHING;

-- name: star_insert_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO resource_stars (username, resource_type, resource_id)
VALUES (?, ?, ?);

-- name: refresh_stars_count
UPDATE resource_social
SET stars_count = (
SELECT COUNT(*)
FROM resource_stars
WHERE resource_type=? AND resource_id=?)
WHERE resource_type=? AND resource_id=?;

-- name: count_stars
SELECT COUNT(*)
FROM resource_stars
WHERE resource_type=? AND resource_id=?;

-- name: star_delete
DELETE FROM resource_stars
WHERE username=? AND resource_type=? AND resource_id=?;
