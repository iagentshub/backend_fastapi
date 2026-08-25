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
-- `email` y `username` son UNIQUE, así que en ambos motores ya tienen su índice.
-- Comprobado con EXPLAIN sobre las 97 consultas que tocan estas tablas: ninguna
-- cambiaba a peor sin un índice explícito.
--
-- Los dos de abajo hacen falta desde que esas dos columnas se comparan con
-- `LOWER()` a los dos lados —el username llega tecleado en la URL del perfil y
-- el email en el formulario de acceso—. Envolver la columna en una función
-- inutiliza el índice del UNIQUE y deja la consulta en escaneo de tabla. Un
-- índice sobre la misma expresión lo devuelve. La sintaxis de índice sobre
-- expresión es idéntica en SQLite y PostgreSQL, así que no lleva marcador de
-- dialecto.
CREATE INDEX IF NOT EXISTS idx_users_lower_username ON users (LOWER(username));
CREATE INDEX IF NOT EXISTS idx_users_lower_email ON users (LOWER(email));
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users (stripe_customer_id);
