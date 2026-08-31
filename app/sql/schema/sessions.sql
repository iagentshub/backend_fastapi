-- sensitive-columns: refresh_hash, prev_refresh_hash
-- El material de renovación de sesión. Son hashes, así que no se puede
-- reconstruir el token a partir de ellos, pero no tienen por qué verse.
-- Sesiones de navegador, una fila por login. El access token la referencia por
-- su claim `sid` y se valida contra esta tabla en cada request autenticado.
--
-- Sin ella, cerrar sesión solo borraba las cookies: el JWT robado seguía siendo
-- válido hasta agotar su `exp`, y no había forma de listar ni cortar sesiones
-- abiertas. Ver docs/adr/008-sesiones-revocables.md.
--
-- `refresh_hash` guarda el SHA-256 del refresh token, nunca el token: quien lea
-- la tabla no puede renovar una sesión con lo que encuentre (mismo criterio que
-- personal_access_tokens).
--
-- `prev_refresh_hash` es lo que hace detectable el robo: la rotación deja ahí el
-- hash anterior, y presentarlo después de rotar significa que dos clientes
-- tienen el mismo refresh. Ante eso se revoca la sesión entera.
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    refresh_hash      TEXT UNIQUE,
    prev_refresh_hash TEXT,
    created_at        TEXT NOT NULL,
    last_seen_at      TEXT,
    expires_at        TEXT NOT NULL,
    revoked_at        TEXT,
    revoked_reason    TEXT,
    ip                TEXT,
    user_agent        TEXT
);
-- El listado de sesiones del perfil ordena por created_at DESC, y la revocación
-- masiva (cambio de contraseña, desactivación de cuenta) filtra por user_id.
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at DESC);
-- La purga barre por caducidad: sin este índice recorre la tabla entera.
CREATE INDEX IF NOT EXISTS idx_sessions_expira ON sessions(expires_at);
-- refresh_hash no lleva índice propio: la columna es UNIQUE y ambos motores ya
-- crean el suyo. prev_refresh_hash no puede ser UNIQUE —queda a NULL en muchas
-- filas y se repite con el refresh_hash rotado— así que lleva el suyo explícito.
CREATE INDEX IF NOT EXISTS idx_sessions_prev_refresh ON sessions(prev_refresh_hash);
