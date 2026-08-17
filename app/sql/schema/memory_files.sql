CREATE TABLE IF NOT EXISTS memory_files (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC);
