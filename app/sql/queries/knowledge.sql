-- Consultas de app/storage/knowledge.py.

-- name: upsert_item
INSERT INTO knowledge_items (id, owner_id, type, title, source, content, char_count, source_char_count, content_truncated, truncation_reason, mime_type, size_bytes, checksum, labels, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO
UPDATE
SET type=excluded.type,title=excluded.title,source=excluded.source,content=excluded.content,char_count=excluded.char_count,source_char_count=excluded.source_char_count,content_truncated=excluded.content_truncated,truncation_reason=excluded.truncation_reason,mime_type=excluded.mime_type,size_bytes=excluded.size_bytes,checksum=excluded.checksum,labels=excluded.labels,updated_at=excluded.updated_at;

-- name: metadata_by_id
SELECT id,owner_id,type,title,labels,is_active,deactivated_at,
       pack_id,pack_relative_path,pack_kind
FROM knowledge_items
WHERE id=?;
