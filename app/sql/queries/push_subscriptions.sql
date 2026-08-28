-- Consultas de app/storage/push_subscriptions.py.

-- El navegador devuelve el mismo endpoint al resuscribirse, así que el alta es
-- idempotente: refresca las claves y el dueño en vez de duplicar la fila.
-- name: upsert_pg
-- engine: pg
INSERT INTO push_subscriptions
    (id, user_id, kind, endpoint, p256dh, auth, user_agent, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (endpoint) DO UPDATE
SET user_id = EXCLUDED.user_id,
    p256dh  = EXCLUDED.p256dh,
    auth    = EXCLUDED.auth;

-- name: upsert_sqlite
-- engine: sqlite
INSERT INTO push_subscriptions
    (id, user_id, kind, endpoint, p256dh, auth, user_agent, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (endpoint) DO UPDATE
SET user_id = excluded.user_id,
    p256dh  = excluded.p256dh,
    auth    = excluded.auth;

-- name: list_for_user
SELECT id, kind, endpoint, p256dh, auth
FROM push_subscriptions
WHERE user_id = ?;

-- name: delete_by_endpoint
DELETE FROM push_subscriptions
WHERE endpoint = ?;

-- name: touch
UPDATE push_subscriptions
SET last_sent_at = ?
WHERE id = ?;

-- name: count_for_user
SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?;
