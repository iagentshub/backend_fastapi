CREATE TABLE IF NOT EXISTS agents (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    -- Fuente oficial de la que salió el recurso, si salió de alguna. Ver
    -- official_sources: es lo que permite filtrarlos y borrarlos en bloque.
    official_source_id    TEXT,
    -- Trazabilidad de solo escritura: se rellena al materializar el recurso y
    -- hoy no la lee nadie —quien necesita ese dato entra por
    -- resource_source_links.component_key, que además tiene UNIQUE por
    -- recurso—. Se conserva a propósito: es el registro de procedencia que
    -- documenta docs/es/api.md, y a NULL no ocupa nada (medido: 0 bytes por
    -- fila en 100.000 filas).
    official_component_id TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agents_official ON agents(official_source_id);
