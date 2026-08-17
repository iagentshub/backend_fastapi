CREATE TABLE IF NOT EXISTS resource_group_shares (
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    group_id  TEXT NOT NULL,
    shared_by     TEXT NOT NULL,
    shared_at     TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_group_share_group ON resource_group_shares(group_id, resource_type);
-- Sin índice propio para (resource_type, resource_id): son el prefijo de la PRIMARY KEY, que en ambos motores ya
-- crea el suyo. Comprobado con EXPLAIN sobre las 97 consultas que tocan
-- estas tablas: ninguna cambia a peor sin el índice explícito.
