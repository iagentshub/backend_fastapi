CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member',
    permissions  TEXT NOT NULL DEFAULT '{}',
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(username);
