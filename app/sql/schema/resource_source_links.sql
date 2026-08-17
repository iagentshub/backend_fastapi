CREATE TABLE IF NOT EXISTS resource_source_links (
    source_id          TEXT NOT NULL,
    component_key      TEXT NOT NULL,
    resource_type      TEXT NOT NULL,
    resource_id        TEXT NOT NULL,
    resource_owner_id  TEXT NOT NULL,
    source_path        TEXT NOT NULL DEFAULT '',
    content_hash       TEXT NOT NULL DEFAULT '',
    commit_sha         TEXT NOT NULL DEFAULT '',
    explicitly_selected @BOOL@ NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (source_id, component_key),
    UNIQUE (resource_type, resource_id, resource_owner_id),
    FOREIGN KEY (source_id) REFERENCES official_sources(id) ON DELETE CASCADE
);
-- Sin índice propio para (resource_type, resource_id, resource_owner_id): son exactamente el UNIQUE de la tabla, que en ambos motores ya
-- crea el suyo. Comprobado con EXPLAIN sobre las 97 consultas que tocan
-- estas tablas: ninguna cambia a peor sin el índice explícito.
