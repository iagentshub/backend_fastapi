CREATE TABLE IF NOT EXISTS knowledge_packs (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    labels      TEXT NOT NULL DEFAULT '["private"]',
    scope       TEXT NOT NULL DEFAULT 'private',
    source_mode TEXT NOT NULL DEFAULT 'upload',
    last_synced_at TEXT,
    upload_status TEXT NOT NULL DEFAULT 'ready',
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_packs_owner
    ON knowledge_packs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_packs_visible_order
    ON knowledge_packs(created_at DESC, id DESC);
