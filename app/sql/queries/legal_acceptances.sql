-- Consultas del consentimiento legal versionado.

-- name: insert_pg
-- engine: pg
INSERT INTO legal_acceptances
(id, user_id, document_type, version, locale, content_sha256, document_url, accepted_at, source)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (user_id, document_type, version) DO NOTHING;

-- name: insert_sqlite
-- engine: sqlite
INSERT INTO legal_acceptances
(id, user_id, document_type, version, locale, content_sha256, document_url, accepted_at, source)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (user_id, document_type, version) DO NOTHING;

-- name: has_current
SELECT 1
FROM legal_acceptances
WHERE user_id = ? AND document_type = ? AND version = ?
LIMIT 1;
