-- gdpr-identity: owner_id
CREATE TABLE IF NOT EXISTS resource_labels (
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    owner_id      TEXT NOT NULL DEFAULT '',
    label         TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id, label)
);

CREATE INDEX IF NOT EXISTS idx_resource_labels_label
    ON resource_labels(label, owner_id);
