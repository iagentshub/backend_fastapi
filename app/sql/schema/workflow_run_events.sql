CREATE TABLE IF NOT EXISTS workflow_run_events (
    run_id      TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY(run_id, sequence),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);
-- Sin índice propio para (run_id, sequence): son la PRIMARY KEY, que en ambos motores ya
-- crea el suyo. Comprobado con EXPLAIN sobre las 97 consultas que tocan
-- estas tablas: ninguna cambia a peor sin el índice explícito.
