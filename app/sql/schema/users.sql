CREATE TABLE IF NOT EXISTS users (
    id                    TEXT PRIMARY KEY,
    username              TEXT UNIQUE NOT NULL,
    email                 TEXT UNIQUE NOT NULL,
    password_hash         TEXT,
    display_name          TEXT,
    birth_date            TEXT,
    gender                TEXT,
    country               TEXT,
    phone                 TEXT,
    provider              TEXT,
    provider_sub          TEXT,
    role                  TEXT NOT NULL DEFAULT 'standard',
    is_active             @BOOL@ NOT NULL DEFAULT 1,
    is_verified           @BOOL@ NOT NULL DEFAULT 1,
    verification_token    TEXT,
    reset_token           TEXT,
    reset_token_expires   TEXT,
    preferences           TEXT,
    deletion_requested_at TEXT,
    deletion_token        TEXT,
    stripe_customer_id    TEXT,
    avatar                TEXT,
    bio                   TEXT,
    languages             TEXT NOT NULL DEFAULT '[]',
    is_email_public       @BOOL@ NOT NULL DEFAULT 0,
    github                TEXT,
    cv                    TEXT,
    created_at            TEXT NOT NULL
);
-- Sin índice propio para email ni username: las dos columnas son UNIQUE, que en ambos motores ya
-- crea el suyo. Comprobado con EXPLAIN sobre las 97 consultas que tocan
-- estas tablas: ninguna cambia a peor sin el índice explícito.
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users (stripe_customer_id);
