CREATE TABLE IF NOT EXISTS official_source_mappings (
    source_id          TEXT NOT NULL,
    source_path        TEXT NOT NULL,
    forced_type        TEXT,
    forced_language    TEXT,
    forced_tool_language TEXT,
    ignored            @BOOL@ NOT NULL DEFAULT 0,
    dependencies       TEXT NOT NULL DEFAULT '[]',
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (source_id, source_path),
    FOREIGN KEY (source_id) REFERENCES official_sources(id) ON DELETE CASCADE
);
