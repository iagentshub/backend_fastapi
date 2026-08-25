-- Consultas de app/storage/contact.py.

-- name: insert_request
INSERT INTO contact_requests (created_at, kind, name, email, message, ip)
VALUES (?, ?, ?, ?, ?, ?);

-- name: list_recent
SELECT id, created_at, kind, name, email, message, ip
FROM contact_requests
ORDER BY created_at DESC, id DESC
LIMIT ?;
