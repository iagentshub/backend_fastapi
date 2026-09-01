CREATE TABLE IF NOT EXISTS legal_acceptances (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('terms', 'privacy')),
    version TEXT NOT NULL,
    locale TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    document_url TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('registration', 'in_session')),
    UNIQUE (user_id, document_type, version)
);
