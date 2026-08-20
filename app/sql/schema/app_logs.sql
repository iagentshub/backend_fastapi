CREATE TABLE IF NOT EXISTS app_logs (
    id       @SERIAL@,
    ts       @FLOAT@ NOT NULL,
    date     TEXT    NOT NULL,
    time     TEXT    NOT NULL,
    ip       TEXT    NOT NULL DEFAULT '-',
    username TEXT    NOT NULL DEFAULT '-',
    level    TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'BE',
    summary  TEXT    NOT NULL,
    category TEXT    NOT NULL DEFAULT 'DIAGNOSTIC',
    action   TEXT,
    resource_type TEXT,
    resource_id   TEXT,
    outcome       TEXT,
    details_json  TEXT
);
-- Los dos índices base conservan el orden de página. `ip`, `username` y
-- `summary` se filtran con LIKE '%x%', que no aprovecha un B-tree. Auditoría
-- añade dos índices compuestos para sus filtros exactos. El de `action` es
-- parcial para no indexar el NULL de cada línea de diagnóstico. Ver
-- docs/adr/007-sql-en-ficheros.md para la retirada de los índices antiguos.
CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_level_ts ON app_logs(level, ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_category_ts ON app_logs(category, ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_action_ts   ON app_logs(action, ts DESC)
WHERE action IS NOT NULL;
