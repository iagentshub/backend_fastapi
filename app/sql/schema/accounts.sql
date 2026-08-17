CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    data        TEXT NOT NULL,
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id, provider);
