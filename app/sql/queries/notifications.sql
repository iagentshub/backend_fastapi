-- Consultas de app/storage/notifications.py.

-- name: insert
INSERT INTO notifications (id, user_id, kind, data, read_at, created_at)
VALUES (?, ?, ?, ?, NULL, ?);

-- name: list_recent
SELECT id, kind, data, read_at, created_at
FROM notifications
WHERE user_id = ?
ORDER BY created_at DESC, id DESC
LIMIT ?;

-- name: count_unread
SELECT COUNT(*)
FROM notifications
WHERE user_id = ? AND read_at IS NULL;

-- name: mark_read
UPDATE notifications
SET read_at = ?
WHERE id = ? AND user_id = ? AND read_at IS NULL
RETURNING id;

-- name: mark_all_read
UPDATE notifications
SET read_at = ?
WHERE user_id = ? AND read_at IS NULL;

-- Las leídas caducan antes que las que nadie ha visto: una leída ya cumplió su
-- función, mientras que una sin leer sigue siendo lo único que le queda al
-- usuario de que aquello ocurrió.
-- `RETURNING` y no rowcount: `AsyncConn.execute` devuelve None en los dos
-- motores, así que contar lo borrado exige que lo devuelva la propia
-- sentencia. Funciona igual en SQLite 3.35+ y en PostgreSQL.
-- name: purge_read
DELETE FROM notifications
WHERE read_at IS NOT NULL AND created_at < ?
RETURNING id;

-- name: purge_unread
DELETE FROM notifications
WHERE read_at IS NULL AND created_at < ?
RETURNING id;
