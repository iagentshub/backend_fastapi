CREATE TABLE IF NOT EXISTS subscription_license_assignments (
    subscription_id TEXT NOT NULL,
    username        TEXT NOT NULL,
    assigned_by     TEXT NOT NULL,
    assigned_at     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (subscription_id, username)
);
CREATE INDEX IF NOT EXISTS idx_license_assignments_sub ON subscription_license_assignments(subscription_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_license_assignments_active_user
    ON subscription_license_assignments(username) WHERE status = 'active';
