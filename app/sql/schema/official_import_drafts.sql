CREATE TABLE IF NOT EXISTS official_import_drafts (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT,
    owner_id            TEXT NOT NULL,
    repository_url      TEXT NOT NULL,
    provider            TEXT NOT NULL,
    repository_path     TEXT NOT NULL,
    tracking_mode       TEXT NOT NULL,
    tracking_ref        TEXT NOT NULL,
    resolved_version    TEXT NOT NULL,
    commit_sha          TEXT NOT NULL,
    source_payload      TEXT NOT NULL,
    errors              TEXT NOT NULL DEFAULT '[]',
    security_warnings   TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'pending',
    expires_at          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES official_sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_official_drafts_source
    ON official_import_drafts(source_id, status, updated_at DESC);
