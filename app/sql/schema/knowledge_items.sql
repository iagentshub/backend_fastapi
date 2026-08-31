CREATE TABLE IF NOT EXISTS knowledge_items (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    source     TEXT NOT NULL,
    content    TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    -- Lo que tenía el original antes de la cota de extracción, y si llegó a
    -- morder. Sin esto, un documento importado a medias es indistinguible de
    -- uno entero: solo se guarda el texto, nunca los bytes de origen.
    source_char_count INTEGER NOT NULL DEFAULT 0,
    content_truncated @BOOL@ NOT NULL DEFAULT 0,
    truncation_reason TEXT NOT NULL DEFAULT '',
    mime_type  TEXT NOT NULL DEFAULT '',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    checksum   TEXT NOT NULL DEFAULT '',
    pack_id    TEXT,
    pack_relative_path TEXT NOT NULL DEFAULT '',
    pack_kind  TEXT NOT NULL DEFAULT '',
    labels     TEXT NOT NULL DEFAULT '["private"]',
    is_active  @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    official_source_id    TEXT,
    -- Trazabilidad de solo escritura. La lectura viva es
    -- resource_source_links.component_key. Ver agents.sql.
    official_component_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_visible_order
    ON knowledge_items(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_official
    ON knowledge_items(official_source_id);
