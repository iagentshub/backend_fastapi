-- La foto de perfil vive fuera de `users` a propósito.
--
-- Era una columna TEXT con el fichero en base64 —un tercio más grande que el
-- original— dentro de la tabla que toca cada petición autenticada. `_USER_COLS`
-- de `app/auth/user_lookup.py` tenía que excluirla a mano y explicar por qué, y
-- cualquier `SELECT *` nuevo la arrastraba entera: megabytes transportados para
-- descartarlos acto seguido. Saber si un usuario tiene foto obligaba además a
-- comparar el contenido (`avatar = ''`), que en PostgreSQL fuerza a traer el
-- valor de su almacenamiento externo solo para ver si está vacío.
--
-- Bytes, no base64: `@BLOB@` es BLOB en SQLite y BYTEA en PostgreSQL. El mismo
-- camino que tomaron los binarios de las tools en `tool_artifacts`.
CREATE TABLE IF NOT EXISTS user_avatars (
    owner_id   TEXT PRIMARY KEY,
    content    @BLOB@ NOT NULL,
    mime       TEXT NOT NULL,
    -- sha256 del contenido. Es el ETag que sirve `GET /api/users/{u}/avatar` y
    -- la versión que viaja en la URL, así que el navegador revalida con un 304
    -- en vez de descargar la imagen entera. Antes la caché se rompía con un
    -- contador que vivía en memoria del cliente y volvía a cero al recargar,
    -- con lo que la URL reaparecía apuntando a la foto anterior.
    checksum   TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
