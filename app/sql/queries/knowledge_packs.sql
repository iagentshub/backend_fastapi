-- Consultas de app/storage/knowledge_packs.py.

-- name: list_pack_files
SELECT k.id, k.pack_relative_path AS relative_path, k.pack_kind AS kind, k.mime_type, k.size_bytes, k.checksum, k.title, k.type, k.char_count, k.source_char_count, k.content_truncated, k.truncation_reason, k.is_active, k.created_at, k.updated_at
FROM knowledge_items k
WHERE k.pack_id=?
ORDER BY k.pack_relative_path;

-- name: get_file_with_pack
SELECT p.*, k.pack_relative_path AS relative_path, k.pack_kind AS kind, k.mime_type, k.size_bytes, k.checksum, 1 AS file_count
FROM knowledge_items k
JOIN knowledge_packs p ON p.id=k.pack_id
WHERE k.id=?;

-- name: pack_item_ids
SELECT id
FROM knowledge_items
WHERE pack_id=?;

-- name: insert_pack
INSERT INTO knowledge_packs (id,owner_id,name,description,labels,scope,source_mode,last_synced_at,upload_status,created_at,updated_at)
VALUES (?,?,?,?,?,'private',?,?,?,?,?);

-- name: insert_pack_item
INSERT INTO knowledge_items (id,owner_id,type,title,source,content,char_count,source_char_count,content_truncated,truncation_reason,mime_type,size_bytes,checksum,pack_id,pack_relative_path,pack_kind,labels,created_at,updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);

-- name: pack_item_by_path
SELECT id
FROM knowledge_items
WHERE pack_id=? AND pack_relative_path=?;

-- name: update_pack_item_content
UPDATE knowledge_items
SET content=?,char_count=?,source_char_count=?,content_truncated=?,truncation_reason=?,pack_kind=?,mime_type=?,size_bytes=?,checksum=?,updated_at=?
WHERE id=?;

-- name: mark_pack_ready
UPDATE knowledge_packs
SET upload_status='ready',last_synced_at=?,updated_at=?
WHERE id=? AND owner_id=?;

-- name: delete_social_by_resource
DELETE FROM resource_social
WHERE resource_type=? AND resource_id=?;

-- name: delete_stars_by_resource
DELETE FROM resource_stars
WHERE resource_type=? AND resource_id=?;

-- name: delete_shares_by_resource
DELETE FROM resource_group_shares
WHERE resource_type=? AND resource_id=?;

-- name: delete_pack_items
DELETE FROM knowledge_items
WHERE pack_id=?;

-- name: delete_pack
DELETE FROM knowledge_packs
WHERE id=?;

-- name: pack_items_for_sync
SELECT id,pack_relative_path,checksum,title,content
FROM knowledge_items
WHERE pack_id=?;

-- name: delete_item
DELETE FROM knowledge_items
WHERE id=?;

-- name: touch_pack_sync
UPDATE knowledge_packs
SET last_synced_at=?,updated_at=?
WHERE id=?;

-- name: update_pack_labels
UPDATE knowledge_packs
SET labels=?,updated_at=?
WHERE id=?;

-- name: update_item_labels
UPDATE knowledge_items
SET labels=?,updated_at=?
WHERE id=?;

-- name: update_pack_metadata
UPDATE knowledge_packs
SET name=?,description=?,labels=?,updated_at=?
WHERE id=?;
