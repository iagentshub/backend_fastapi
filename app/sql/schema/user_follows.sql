-- gdpr-identity: follower, following
CREATE TABLE IF NOT EXISTS user_follows (
    follower   TEXT NOT NULL,
    following  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT @NOW@,
    PRIMARY KEY (follower, following)
);

CREATE INDEX IF NOT EXISTS idx_uf_follower
    ON user_follows(follower);
CREATE INDEX IF NOT EXISTS idx_uf_following
    ON user_follows(following);
