-- Consultas de app/storage/billing.py.

-- name: latest_by_username
SELECT *
FROM subscriptions
WHERE username = ?
ORDER BY updated_at DESC
LIMIT 1;

-- name: get_by_id
SELECT *
FROM subscriptions
WHERE id = ?;

-- name: get_by_id_for_update
-- engine: pg
SELECT *
FROM subscriptions
WHERE id = ?
FOR UPDATE;

-- name: get_by_stripe_id
SELECT *
FROM subscriptions
WHERE stripe_subscription_id = ?;

-- name: update_by_stripe_id
UPDATE subscriptions
SET username=?, stripe_customer_id=?, tier=?, seats=?, self_hosted=?, interval=?, amount_cents=?, status=?, current_period_end=?, cancel_at_period_end=?, updated_at=?
WHERE stripe_subscription_id=?;

-- name: insert_subscription
INSERT INTO subscriptions (id, username, stripe_customer_id, stripe_subscription_id, tier, seats, self_hosted, interval, amount_cents, status, current_period_end, cancel_at_period_end, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: set_cancel_at_period_end
UPDATE subscriptions
SET cancel_at_period_end=?, updated_at=?
WHERE stripe_subscription_id=?;

-- name: count_active_assignments
SELECT COUNT(*)
FROM subscription_license_assignments
WHERE subscription_id = ? AND status = 'active';

-- name: has_active_license
SELECT 1
FROM subscription_license_assignments la
JOIN subscriptions s ON s.id = la.subscription_id
WHERE la.username = ? AND la.status = 'active' AND s.status NOT IN (?, ?)
LIMIT 1;

-- name: list_assignments
SELECT la.username AS user_id, u.username, la.assigned_by, la.assigned_at, la.status, u.email, u.role, u.is_active
FROM subscription_license_assignments la
LEFT JOIN users u ON u.id = la.username
WHERE la.subscription_id = ?
ORDER BY la.status ASC, la.assigned_at ASC;

-- name: list_users
-- Sin invitados: no tienen licencia ni pueden tenerla.
SELECT id, username, email, role, is_active
FROM users
WHERE role <> 'guest'
ORDER BY username ASC;

-- name: user_exists
SELECT 1
FROM users
WHERE id = ?;

-- name: active_assignment_for_user
SELECT *
FROM subscription_license_assignments
WHERE username = ? AND status = 'active';

-- name: assign_license_pg
-- engine: pg
INSERT INTO subscription_license_assignments (subscription_id, username, assigned_by, assigned_at, status)
VALUES (?, ?, ?, ?, 'active')
ON CONFLICT (subscription_id, username) DO
UPDATE
SET assigned_by = EXCLUDED.assigned_by, assigned_at = EXCLUDED.assigned_at, status = 'active';

-- name: assign_license_sqlite
-- engine: sqlite
INSERT INTO subscription_license_assignments (subscription_id, username, assigned_by, assigned_at, status)
VALUES (?, ?, ?, ?, 'active')
ON CONFLICT (subscription_id, username) DO
UPDATE
SET assigned_by = excluded.assigned_by, assigned_at = excluded.assigned_at, status = 'active';

-- name: get_assignment
SELECT *
FROM subscription_license_assignments
WHERE subscription_id = ? AND username = ?;

-- name: assignment_is_active
SELECT 1
FROM subscription_license_assignments
WHERE subscription_id = ? AND username = ? AND status = 'active';

-- name: revoke_assignment
UPDATE subscription_license_assignments
SET status = 'revoked'
WHERE subscription_id = ? AND username = ?;

-- name: claim_stripe_event_pg
-- Reserva el evento y dice si la ha ganado: sin fila devuelta, ya estaba.
INSERT INTO stripe_events (stripe_event_id, type, processed_at, payload)
VALUES (?, ?, ?, ?)
ON CONFLICT (stripe_event_id) DO NOTHING
RETURNING stripe_event_id;

-- name: claim_stripe_event_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO stripe_events (stripe_event_id, type, processed_at, payload)
VALUES (?, ?, ?, ?)
RETURNING stripe_event_id;

-- name: delete_stripe_event
DELETE FROM stripe_events
WHERE stripe_event_id = ?;

-- name: purge_stripe_events
DELETE FROM stripe_events
WHERE processed_at < ?
RETURNING stripe_event_id;
