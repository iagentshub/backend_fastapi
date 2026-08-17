CREATE TABLE IF NOT EXISTS resource_versions (
    id            TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    snapshot      TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT 'save',
    created_at    TEXT NOT NULL,
    UNIQUE(resource_type, resource_id, owner_id, version)
);
-- Sin índice propio para la búsqueda por recurso y versión: esas columnas son el UNIQUE de la tabla, que en ambos motores ya
-- crea el suyo. Comprobado con EXPLAIN sobre las 97 consultas que tocan
-- estas tablas: ninguna cambia a peor sin el índice explícito.
