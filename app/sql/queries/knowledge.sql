-- Consultas de app/storage/knowledge.py.

-- name: upsert_item
INSERT INTO knowledge_items (id, owner_id, type, title, source, content, char_count, mime_type, size_bytes, checksum, labels, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO
UPDATE
SET type=excluded.type,title=excluded.title,source=excluded.source,content=excluded.content,char_count=excluded.char_count,mime_type=excluded.mime_type,size_bytes=excluded.size_bytes,checksum=excluded.checksum,labels=excluded.labels,updated_at=excluded.updated_at;
