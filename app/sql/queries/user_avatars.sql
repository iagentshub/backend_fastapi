-- Consultas de app/storage/avatars.py.

-- name: upsert_sqlite
-- engine: sqlite
INSERT OR REPLACE INTO user_avatars
    (owner_id, content, mime, checksum, size_bytes, updated_at)
VALUES (?, ?, ?, ?, ?, ?);

-- name: upsert_pg
-- engine: pg
INSERT INTO user_avatars
    (owner_id, content, mime, checksum, size_bytes, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (owner_id) DO UPDATE SET
    content = EXCLUDED.content,
    mime = EXCLUDED.mime,
    checksum = EXCLUDED.checksum,
    size_bytes = EXCLUDED.size_bytes,
    updated_at = EXCLUDED.updated_at;

-- name: content_of
-- Por username y no por id: la ruta pública es /api/users/{username}/avatar.
-- Sin invitados, como el resto del perfil público.
SELECT a.content, a.mime, a.checksum
FROM user_avatars a
JOIN users u ON u.id = a.owner_id
WHERE LOWER(u.username) = LOWER(?) AND u.role <> 'guest';

-- name: checksum_of
-- Solo el hash: es lo que decide el ETag y la versión de la URL, y así una
-- pantalla que solo necesita saber si hay foto no arrastra la imagen entera.
SELECT a.checksum
FROM user_avatars a
JOIN users u ON u.id = a.owner_id
WHERE LOWER(u.username) = LOWER(?) AND u.role <> 'guest';

-- name: checksum_by_owner
SELECT checksum
FROM user_avatars
WHERE owner_id = ?;

-- name: delete_of
DELETE FROM user_avatars
WHERE owner_id = ?;
