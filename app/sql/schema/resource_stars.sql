-- gdpr-identity: username
CREATE TABLE IF NOT EXISTS resource_stars (
    username      TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT @NOW@,
    PRIMARY KEY (username, resource_type, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_rs_resource
    ON resource_stars(resource_type, resource_id);
