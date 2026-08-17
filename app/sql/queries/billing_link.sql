-- Consultas de app/services/billing_link.py.

-- name: stripe_customer_of
SELECT stripe_customer_id
FROM users
WHERE id = ? OR username = ?;

-- name: set_stripe_customer
UPDATE users
SET stripe_customer_id = ?
WHERE id = ? OR username = ?;

-- name: user_by_stripe_customer
SELECT id
FROM users
WHERE stripe_customer_id = ?;
