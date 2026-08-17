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
-- Este índice no lo elige ninguna consulta: los borradores se leen por `id` y se
-- barren por `expires_at`. Se conserva por la FOREIGN KEY de arriba: PostgreSQL no
-- indexa las claves foráneas por su cuenta, así que sin él borrar una fila de
-- official_sources recorre entera esta tabla por cada cascada. Es la única FK del
-- esquema que no queda cubierta por el prefijo de una PRIMARY KEY.
CREATE INDEX IF NOT EXISTS idx_official_drafts_source
    ON official_import_drafts(source_id, status, updated_at DESC);
-- Por aquí sí entran consultas: count_expired_drafts y delete_expired_drafts.
CREATE INDEX IF NOT EXISTS idx_official_drafts_expires
    ON official_import_drafts(expires_at);
