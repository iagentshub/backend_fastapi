-- Fuentes del contenido oficial. Lo que traen no vive aquí: se materializa
-- como recurso normal (agents, skills, …) marcado con official_source_id, de
-- modo que "oficial" sea solo una etiqueta y no un tipo de objeto aparte.
CREATE TABLE IF NOT EXISTS official_sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    repository_url      TEXT NOT NULL UNIQUE,
    repository_owner    TEXT NOT NULL DEFAULT '',
    repository_name     TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT 'github',
    repository_path     TEXT NOT NULL DEFAULT '',
    owner_id            TEXT,
    default_branch      TEXT NOT NULL DEFAULT 'main',
    tracking_mode       TEXT NOT NULL DEFAULT 'release',
    tracking_ref        TEXT NOT NULL DEFAULT 'main',
    import_mode         TEXT NOT NULL DEFAULT 'deterministic',
    llm_connection_id   TEXT,
    license             TEXT NOT NULL DEFAULT '',
    last_version        TEXT,
    last_commit_sha     TEXT,
    sync_state          TEXT NOT NULL DEFAULT 'idle',
    latest_checked_at   TEXT,
    last_sync_error     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
