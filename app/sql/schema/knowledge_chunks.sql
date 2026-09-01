CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id            TEXT PRIMARY KEY,
    knowledge_id  TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    @KNOWLEDGE_SEARCH_COLUMN@
    UNIQUE (knowledge_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
    ON knowledge_chunks(knowledge_id, chunk_index);
@KNOWLEDGE_SEARCH_INDEX@
