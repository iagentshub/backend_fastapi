CREATE TABLE IF NOT EXISTS official_import_components (
    draft_id            TEXT NOT NULL,
    component_key       TEXT NOT NULL,
    payload             TEXT NOT NULL,
    selected            @BOOL@ NOT NULL DEFAULT 0,
    explicitly_selected @BOOL@ NOT NULL DEFAULT 0,
    forced_type         TEXT,
    forced_language     TEXT,
    forced_tool_language TEXT,
    security_accepted   @BOOL@ NOT NULL DEFAULT 0,
    state               TEXT NOT NULL DEFAULT 'new',
    PRIMARY KEY (draft_id, component_key),
    FOREIGN KEY (draft_id) REFERENCES official_import_drafts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_official_components_filter
    ON official_import_components(draft_id, state, selected);
