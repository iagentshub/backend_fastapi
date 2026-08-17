-- Consultas de app/storage/official_source_storage.py.

-- name: list_all
SELECT *
FROM official_sources
ORDER BY lower(name);

-- name: get_by_id
SELECT *
FROM official_sources
WHERE id=?;

-- name: get_by_url
SELECT *
FROM official_sources
WHERE repository_url=?;

-- name: update_from_repo
UPDATE official_sources
SET name=?, description=?, repository_owner=?, repository_name=?, provider=?, repository_path=?, owner_id=COALESCE(owner_id, ?), default_branch=?, tracking_mode=?, tracking_ref=?, import_mode=?, llm_connection_id=?, license=?, updated_at=?
WHERE id=?;

-- name: insert_full
INSERT INTO official_sources (id,name,description,repository_url,repository_owner,repository_name,provider,repository_path,owner_id,default_branch,tracking_mode,tracking_ref,import_mode,llm_connection_id,license,created_at,updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);

-- name: url_taken_by_other
SELECT id
FROM official_sources
WHERE repository_url=? AND id<>?;

-- name: update_fields
UPDATE official_sources
SET name=?, description=?, repository_url=?, repository_owner=?, repository_name=?, provider=?, repository_path=?, default_branch=?, tracking_mode=?, tracking_ref=?, license=?, updated_at=?
WHERE id=?;

-- name: update_sync_result
UPDATE official_sources
SET latest_checked_at=?, last_sync_error=?, last_version=COALESCE(?, last_version), last_commit_sha=COALESCE(?, last_commit_sha), sync_state=?, updated_at=?
WHERE id=?;

-- name: claim_applying
UPDATE official_sources
SET sync_state='applying',updated_at=?
WHERE id=? AND sync_state<>'applying' AND COALESCE(last_commit_sha,'')=?
RETURNING id;

-- name: delete_source
DELETE FROM official_sources
WHERE id=?;

-- name: insert_minimal
INSERT INTO official_sources (id,name,description,repository_url,provider,repository_path,tracking_mode,tracking_ref,created_at,updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?);

-- name: upsert_link
INSERT INTO resource_source_links (source_id,component_key,resource_type,resource_id,resource_owner_id,source_path,content_hash,commit_sha,explicitly_selected,created_at,updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(source_id,component_key) DO
UPDATE
SET resource_type=excluded.resource_type,resource_id=excluded.resource_id,resource_owner_id=excluded.resource_owner_id,source_path=excluded.source_path,content_hash=excluded.content_hash,commit_sha=excluded.commit_sha,explicitly_selected=excluded.explicitly_selected,updated_at=excluded.updated_at;

-- name: delete_link_by_resource
DELETE FROM resource_source_links
WHERE resource_type=? AND resource_id=? AND resource_owner_id=?;

-- name: get_link
SELECT resource_type,resource_id,resource_owner_id,source_path,content_hash,commit_sha,explicitly_selected
FROM resource_source_links
WHERE source_id=? AND component_key=?;

-- name: list_links
SELECT component_key,resource_type,resource_id,resource_owner_id,source_path,content_hash,commit_sha,explicitly_selected,created_at,updated_at
FROM resource_source_links
WHERE source_id=?
ORDER BY component_key;

-- name: set_owner
UPDATE official_sources
SET owner_id=?,updated_at=?
WHERE id=?;

-- name: relabel_labels_owner
UPDATE resource_labels
SET owner_id=?
WHERE resource_type=? AND resource_id=? AND owner_id=?;

-- name: relabel_social_owner
UPDATE resource_social
SET owner=?
WHERE resource_type=? AND resource_id=? AND owner=?;

-- name: relabel_versions_owner
UPDATE resource_versions
SET owner_id=?
WHERE resource_type=? AND resource_id=? AND owner_id=?;

-- name: relabel_links_owner
UPDATE resource_source_links
SET resource_owner_id=?
WHERE source_id=?;

-- name: insert_draft
INSERT INTO official_import_drafts (id,source_id,owner_id,repository_url,provider,repository_path,tracking_mode,tracking_ref,resolved_version,commit_sha,source_payload,errors,security_warnings,status,expires_at,created_at,updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);

-- name: insert_draft_component
INSERT INTO official_import_components (draft_id,component_key,payload,selected,explicitly_selected,forced_type,forced_language,forced_tool_language,security_accepted,state)
VALUES (?,?,?,?,?,?,?,?,?,?);

-- name: get_draft
SELECT *
FROM official_import_drafts
WHERE id=?;

-- name: count_draft_components
SELECT COUNT(*)
FROM official_import_components
WHERE draft_id=?;

-- name: update_draft_source
UPDATE official_import_drafts
SET source_id=?,source_payload=?,updated_at=?
WHERE id=?;

-- name: get_component_payload
SELECT payload
FROM official_import_components
WHERE draft_id=? AND component_key=?;

-- name: update_component_payload
UPDATE official_import_components
SET payload=?
WHERE draft_id=? AND component_key=?;

-- name: touch_draft
UPDATE official_import_drafts
SET updated_at=?
WHERE id=?;

-- name: get_component
SELECT *
FROM official_import_components
WHERE draft_id=? AND component_key=?;

-- name: list_components
SELECT *
FROM official_import_components
WHERE draft_id=?
ORDER BY component_key;

-- name: list_component_keys
SELECT component_key
FROM official_import_components
WHERE draft_id=?;

-- name: set_component_selection
UPDATE official_import_components
SET selected=?,explicitly_selected=?
WHERE draft_id=? AND component_key=?;

-- name: upsert_mapping
INSERT INTO official_source_mappings (source_id,source_path,forced_type,forced_language,forced_tool_language,ignored,dependencies,updated_at)
VALUES (?,?,?,?,?,?,?,?)
ON CONFLICT(source_id,source_path) DO
UPDATE
SET forced_type=excluded.forced_type,forced_language=excluded.forced_language,ignored=excluded.ignored,forced_tool_language=excluded.forced_tool_language,dependencies=excluded.dependencies,updated_at=excluded.updated_at;

-- name: list_mappings
SELECT *
FROM official_source_mappings
WHERE source_id=?;

-- name: set_draft_status
UPDATE official_import_drafts
SET status=?,updated_at=?
WHERE id=?;

-- name: count_expired_drafts
SELECT COUNT(*)
FROM official_import_drafts
WHERE expires_at<?;

-- name: delete_expired_drafts
DELETE FROM official_import_drafts
WHERE expires_at<?;

-- name: agents_not_from_source
SELECT id,name,data
FROM agents
WHERE owner_id=? AND COALESCE(official_source_id,'')<>?;
