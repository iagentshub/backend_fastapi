CREATE TABLE IF NOT EXISTS connections (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    provider_account_id TEXT,
    name        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
-- Estaba declarado suelto en db.py::migrate_schema, dentro de la rama de
-- SQLite: PostgreSQL nunca lo llegaba a crear. Aquí lo obtienen los dos.
CREATE INDEX IF NOT EXISTS idx_connections_owner ON connections(owner_id);
CREATE INDEX IF NOT EXISTS idx_connections_updated_page
    ON connections(updated_at DESC, id);
