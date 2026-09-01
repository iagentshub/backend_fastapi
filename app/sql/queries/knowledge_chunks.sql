-- Consultas de sincronización de app/storage/knowledge_chunks.py.

-- name: delete_by_knowledge
DELETE FROM knowledge_chunks
WHERE knowledge_id=?;

-- name: insert_chunk
INSERT INTO knowledge_chunks (id,knowledge_id,chunk_index,title,content)
VALUES (?,?,?,?,?);

-- name: update_title
UPDATE knowledge_chunks
SET title=?
WHERE knowledge_id=?;
