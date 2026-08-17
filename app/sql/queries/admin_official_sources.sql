-- Consultas de app/api/routes/admin/official_sources.py.

-- name: active_admin_exists
SELECT id
FROM users
WHERE id=? AND role='admin' AND is_active=1;
