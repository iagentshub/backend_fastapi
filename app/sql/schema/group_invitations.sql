CREATE TABLE IF NOT EXISTS group_invitations (
    id           TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    invited_by   TEXT NOT NULL,
    username     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    UNIQUE(group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_group_inv_user ON group_invitations(username, status);
