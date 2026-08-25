-- Consultas de app/storage/tool_storage.py.

-- name: upsert_pg
-- engine: pg
INSERT INTO tools (id, owner_id, name, language, scope, data, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=EXCLUDED.name, language=EXCLUDED.language, scope=EXCLUDED.scope, data=EXCLUDED.data, content=EXCLUDED.content, updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
INSERT INTO tools (id, owner_id, name, language, scope, data, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO
UPDATE
SET name=excluded.name, language=excluded.language, scope=excluded.scope, data=excluded.data, content=excluded.content, updated_at=excluded.updated_at;

-- name: list_public
SELECT id, owner_id, name, language, scope, data, binary_filename, binary_size, binary_uploaded_at, CASE WHEN content <> '' THEN 1 ELSE 0 END AS has_content, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE scope='public'
ORDER BY created_at ASC;

-- name: list_private_by_owner
SELECT id, owner_id, name, language, scope, data, binary_filename, binary_size, binary_uploaded_at, CASE WHEN content <> '' THEN 1 ELSE 0 END AS has_content, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE scope='private' AND owner_id=?
ORDER BY created_at ASC;

-- name: list_private
SELECT id, owner_id, name, language, scope, data, binary_filename, binary_size, binary_uploaded_at, CASE WHEN content <> '' THEN 1 ELSE 0 END AS has_content, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE scope='private'
ORDER BY created_at ASC;

-- name: list_all
SELECT id, owner_id, name, language, scope, data, binary_filename, binary_size, binary_uploaded_at, CASE WHEN content <> '' THEN 1 ELSE 0 END AS has_content, is_active, deactivated_at, created_at, updated_at
FROM tools
ORDER BY created_at ASC;

-- name: get_scoped
SELECT id, owner_id, name, language, scope, data, content, binary_filename, binary_size, binary_uploaded_at, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE id=? AND scope=?
LIMIT 1;

-- name: get_scoped_owned
SELECT id, owner_id, name, language, scope, data, content, binary_filename, binary_size, binary_uploaded_at, is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE id=? AND scope=? AND owner_id=?
LIMIT 1;

-- name: list_by_ids
SELECT id, owner_id, name, language, scope, data, binary_filename, binary_size,
       binary_uploaded_at,
       CASE WHEN content <> '' THEN 1 ELSE 0 END AS has_content,
       is_active, deactivated_at, created_at, updated_at
FROM tools
WHERE id IN (@tool_ids@)
ORDER BY CASE WHEN scope='public' THEN 0 ELSE 1 END, created_at ASC;

-- name: exists_scoped_owned
SELECT id, owner_id
FROM tools
WHERE id=? AND scope=? AND owner_id=?
LIMIT 1;

-- name: delete_scoped_owned
DELETE FROM tools
WHERE id=? AND scope=? AND owner_id=?;

-- name: exists_scoped
SELECT id, owner_id
FROM tools
WHERE id=? AND scope=?
LIMIT 1;

-- name: delete_scoped
DELETE FROM tools
WHERE id=? AND scope=?;

-- name: binary_target_owned
SELECT owner_id, data
FROM tools
WHERE id=? AND owner_id=?
LIMIT 1;

-- name: binary_target
SELECT owner_id, data
FROM tools
WHERE id=?
LIMIT 1;

-- name: get_binary
SELECT binary_b64, binary_filename, binary_size, binary_uploaded_at, data
FROM tools
WHERE id=? AND scope=?
LIMIT 1;

-- name: get_binary_owned
SELECT binary_b64, binary_filename, binary_size, binary_uploaded_at, data
FROM tools
WHERE id=? AND scope=? AND owner_id=?
LIMIT 1;

-- name: clear_binary_owned
UPDATE tools
SET binary_b64=NULL, binary_filename=NULL, binary_size=NULL, binary_uploaded_at=NULL, data=?, updated_at=?
WHERE id=? AND owner_id=?;

-- name: clear_binary
UPDATE tools
SET binary_b64=NULL, binary_filename=NULL, binary_size=NULL, binary_uploaded_at=NULL, data=?, updated_at=?
WHERE id=?;

-- name: get_artifact
SELECT artifact.binary_data, artifact.size, link.sha256,
       tool.binary_filename, tool.binary_uploaded_at, tool.data
FROM tools AS tool
JOIN tool_artifact_links AS link
  ON link.tool_id=tool.id AND link.owner_id=tool.owner_id
JOIN tool_artifacts AS artifact ON artifact.sha256=link.sha256
WHERE tool.id=? AND tool.scope=?
LIMIT 1;

-- name: get_artifact_owned
SELECT artifact.binary_data, artifact.size, link.sha256,
       tool.binary_filename, tool.binary_uploaded_at, tool.data
FROM tools AS tool
JOIN tool_artifact_links AS link
  ON link.tool_id=tool.id AND link.owner_id=tool.owner_id
JOIN tool_artifacts AS artifact ON artifact.sha256=link.sha256
WHERE tool.id=? AND tool.scope=? AND tool.owner_id=?
LIMIT 1;

-- name: insert_artifact_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO tool_artifacts (sha256, binary_data, size, created_at)
VALUES (?, ?, ?, ?);

-- name: insert_artifact_pg
-- engine: pg
INSERT INTO tool_artifacts (sha256, binary_data, size, created_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (sha256) DO NOTHING;

-- name: link_artifact_sqlite
-- engine: sqlite
INSERT INTO tool_artifact_links (tool_id, owner_id, sha256)
VALUES (?, ?, ?)
ON CONFLICT (tool_id, owner_id) DO UPDATE SET sha256=excluded.sha256;

-- name: link_artifact_pg
-- engine: pg
INSERT INTO tool_artifact_links (tool_id, owner_id, sha256)
VALUES (?, ?, ?)
ON CONFLICT (tool_id, owner_id) DO UPDATE SET sha256=EXCLUDED.sha256;

-- name: retain_version_artifact_sqlite
-- engine: sqlite
INSERT INTO tool_version_artifacts (version_id, sha256)
VALUES (?, ?)
ON CONFLICT (version_id) DO UPDATE SET sha256=excluded.sha256;

-- name: retain_version_artifact_pg
-- engine: pg
INSERT INTO tool_version_artifacts (version_id, sha256)
VALUES (?, ?)
ON CONFLICT (version_id) DO UPDATE SET sha256=EXCLUDED.sha256;

-- name: get_version_artifact
SELECT artifact.sha256, artifact.size
FROM tool_version_artifacts AS version_artifact
JOIN tool_artifacts AS artifact ON artifact.sha256=version_artifact.sha256
WHERE version_artifact.version_id=?
LIMIT 1;

-- name: set_binary_metadata_owned
UPDATE tools
SET binary_b64=NULL, binary_filename=?, binary_size=?, binary_uploaded_at=?, data=?, updated_at=?
WHERE id=? AND owner_id=?;

-- name: set_binary_metadata
UPDATE tools
SET binary_b64=NULL, binary_filename=?, binary_size=?, binary_uploaded_at=?, data=?, updated_at=?
WHERE id=?;

-- name: unlink_artifact_owned
DELETE FROM tool_artifact_links WHERE tool_id=? AND owner_id=?;

-- name: unlink_artifact
DELETE FROM tool_artifact_links WHERE tool_id=?;

-- name: delete_orphan_artifacts
DELETE FROM tool_artifacts
WHERE NOT EXISTS (
    SELECT 1 FROM tool_artifact_links AS link
    WHERE link.sha256=tool_artifacts.sha256
)
AND NOT EXISTS (
    SELECT 1 FROM tool_version_artifacts AS version_artifact
    WHERE version_artifact.sha256=tool_artifacts.sha256
);
