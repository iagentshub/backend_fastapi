-- gdpr-identity: owner
CREATE TABLE IF NOT EXISTS resource_social (
    resource_type      TEXT NOT NULL,
    resource_id        TEXT NOT NULL,
    owner              TEXT NOT NULL,
    name               TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    is_public          @BOOL@ NOT NULL DEFAULT 0,
    category           TEXT NOT NULL DEFAULT 'Other',
    trial_missing_deps TEXT NOT NULL DEFAULT 'warn',
    linked_to_user     TEXT,
    linked_to_id       TEXT,
    stars_count        INTEGER NOT NULL DEFAULT 0,
    tags               TEXT NOT NULL DEFAULT '[]',
    labels             TEXT NOT NULL DEFAULT '["private"]',
    verified           @BOOL@ NOT NULL DEFAULT 0,
    updated_at         TEXT NOT NULL DEFAULT @NOW@,
    PRIMARY KEY (resource_type, resource_id, owner)
);

CREATE INDEX IF NOT EXISTS idx_rsoc_public
    ON resource_social(is_public, resource_type, category);
CREATE INDEX IF NOT EXISTS idx_rsoc_owner
    ON resource_social(owner, resource_type);
CREATE INDEX IF NOT EXISTS idx_rsoc_link_origin
    ON resource_social(owner, linked_to_user, linked_to_id, resource_type)
    WHERE linked_to_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rsoc_public_page
    ON resource_social(
        is_public, resource_type, updated_at DESC, stars_count DESC, resource_id
    );
