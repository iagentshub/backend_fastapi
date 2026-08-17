CREATE TABLE IF NOT EXISTS app_logs (
    id       @SERIAL@,
    ts       @FLOAT@ NOT NULL,
    date     TEXT    NOT NULL,
    time     TEXT    NOT NULL,
    ip       TEXT    NOT NULL DEFAULT '-',
    username TEXT    NOT NULL DEFAULT '-',
    level    TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'BE',
    summary  TEXT    NOT NULL
);
-- Dos índices, no seis. El visor (app/api/routes/logs.py) siempre ordena por
-- `ts DESC` con LIMIT, y filtra `ip`, `username` y `summary` con LIKE '%x%':
-- el comodín inicial impide usar un B-tree, así que sus índices no se podían
-- elegir nunca. Los de `level` y `source` sí se elegían, y ahí estaba el daño:
-- al entrar por ellos se pierde el orden de `ts` y hay que ordenar el
-- resultado entero (18 ms para filtrar por fuente, frente a 0,06 con este
-- par). Medido sobre 200.000 filas con ERROR al 1%: la escritura baja un 66% y
-- la base ocupa un 27% menos. Ver docs/adr/007-sql-en-ficheros.md.
CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_level_ts ON app_logs(level, ts DESC);
