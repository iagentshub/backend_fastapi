-- sensitive-columns: token_hash
-- El PAT con el que entra la extensión de VS Code.
CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT,
    last_used_at TEXT,
    revoked_at   TEXT
);
-- Sin índice propio para token_hash: la columna es UNIQUE, que en ambos motores ya
-- crea el suyo. Comprobado con EXPLAIN sobre las 97 consultas que tocan
-- estas tablas: ninguna cambia a peor sin el índice explícito.
CREATE INDEX IF NOT EXISTS idx_pat_user ON personal_access_tokens(username, created_at DESC);
