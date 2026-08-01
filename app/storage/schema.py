"""DDL base para SQLite y PostgreSQL.

Las migraciones incrementales permanecen en :mod:`app.storage.db`.
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS memory_files (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS connections (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    owner_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    data        TEXT NOT NULL,
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (owner_id, provider)
);
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user_agent
    ON conversations(user_id, agent_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv
    ON messages(conversation_id, created_at ASC);
CREATE TABLE IF NOT EXISTS knowledge_items (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    source     TEXT NOT NULL,
    content    TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC);
CREATE TABLE IF NOT EXISTS users (
    id                    TEXT PRIMARY KEY,
    username              TEXT UNIQUE NOT NULL,
    email                 TEXT UNIQUE NOT NULL,
    password_hash         TEXT,
    display_name          TEXT,
    birth_date            TEXT,
    gender                TEXT,
    country               TEXT,
    phone                 TEXT,
    provider              TEXT,
    provider_sub          TEXT,
    role                  TEXT NOT NULL DEFAULT 'standard',
    is_active             INTEGER NOT NULL DEFAULT 1,
    is_verified           INTEGER NOT NULL DEFAULT 1,
    verification_token    TEXT,
    reset_token           TEXT,
    reset_token_expires   TEXT,
    preferences           TEXT,
    deletion_requested_at TEXT,
    deletion_token        TEXT,
    stripe_customer_id    TEXT,
    avatar                TEXT,
    bio                   TEXT,
    languages             TEXT NOT NULL DEFAULT '[]',
    is_email_public       INTEGER NOT NULL DEFAULT 0,
    github                TEXT,
    cv                    TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users (stripe_customer_id);
CREATE TABLE IF NOT EXISTS resource_group_shares (
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    group_id  TEXT NOT NULL,
    shared_by     TEXT NOT NULL,
    shared_at     TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_group_share_group ON resource_group_shares(group_id, resource_type);
CREATE INDEX IF NOT EXISTS idx_group_share_resource  ON resource_group_shares(resource_type, resource_id);
CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member',
    permissions  TEXT NOT NULL DEFAULT '{}',
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(username);
CREATE TABLE IF NOT EXISTS token_daily (
    day      TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    tokens   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC);
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
CREATE TABLE IF NOT EXISTS subscriptions (
    id                     TEXT PRIMARY KEY,
    username               TEXT NOT NULL,
    stripe_customer_id     TEXT NOT NULL,
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    tier                   TEXT NOT NULL,
    seats                  INTEGER NOT NULL DEFAULT 1,
    self_hosted            INTEGER NOT NULL DEFAULT 0,
    interval               TEXT NOT NULL,
    amount_cents           INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL,
    current_period_end     TEXT,
    cancel_at_period_end   INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_username ON subscriptions(username);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(stripe_customer_id);
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
CREATE TABLE IF NOT EXISTS stripe_events (
    stripe_event_id TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    processed_at    TEXT NOT NULL,
    payload         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    date     TEXT    NOT NULL,
    time     TEXT    NOT NULL,
    ip       TEXT    NOT NULL DEFAULT '-',
    username TEXT    NOT NULL DEFAULT '-',
    level    TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'BE',
    summary  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_date     ON app_logs(date);
CREATE INDEX IF NOT EXISTS idx_al_level    ON app_logs(level);
CREATE INDEX IF NOT EXISTS idx_al_username ON app_logs(username);
CREATE INDEX IF NOT EXISTS idx_al_ip       ON app_logs(ip);
CREATE INDEX IF NOT EXISTS idx_al_source   ON app_logs(source);
CREATE TABLE IF NOT EXISTS user_agent_preferences (
    username      TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    connection_id TEXT,
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (username, agent_id)
);
CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pat_hash ON personal_access_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_pat_user ON personal_access_tokens(username, created_at DESC);
CREATE TABLE IF NOT EXISTS vscode_auth_codes (
    code_hash  TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    state      TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_versions (
    id            TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    snapshot      TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT 'save',
    created_at    TEXT NOT NULL,
    UNIQUE(resource_type, resource_id, owner_id, version)
);
CREATE INDEX IF NOT EXISTS idx_resource_versions_lookup
    ON resource_versions(resource_type, resource_id, owner_id, version DESC);
CREATE TABLE IF NOT EXISTS agent_workflows (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition  TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'private',
    labels      TEXT NOT NULL DEFAULT '["private"]',
    is_active   INTEGER NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY(id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_workflows_owner
    ON agent_workflows(owner_id, updated_at DESC);
"""

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   SMALLINT NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    is_active   SMALLINT NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS memory_files (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory_files(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS connections (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   SMALLINT NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    owner_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    data        TEXT NOT NULL,
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (owner_id, provider)
);
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user_agent
    ON conversations(user_id, agent_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv
    ON messages(conversation_id, created_at ASC);
CREATE TABLE IF NOT EXISTS knowledge_items (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    source     TEXT NOT NULL,
    content    TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    is_active  SMALLINT NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC);
CREATE TABLE IF NOT EXISTS users (
    id                 TEXT PRIMARY KEY,
    username           TEXT UNIQUE NOT NULL,
    email              TEXT UNIQUE NOT NULL,
    password_hash      TEXT,
    display_name       TEXT,
    birth_date         TEXT,
    gender             TEXT,
    country            TEXT,
    phone              TEXT,
    provider           TEXT,
    provider_sub       TEXT,
    role               TEXT NOT NULL DEFAULT 'standard',
    is_active             SMALLINT NOT NULL DEFAULT 1,
    is_verified           SMALLINT NOT NULL DEFAULT 1,
    verification_token    TEXT,
    reset_token           TEXT,
    reset_token_expires   TEXT,
    preferences           TEXT,
    deletion_requested_at TEXT,
    deletion_token        TEXT,
    stripe_customer_id    TEXT,
    avatar                TEXT,
    bio                   TEXT,
    languages             TEXT NOT NULL DEFAULT '[]',
    is_email_public       SMALLINT NOT NULL DEFAULT 0,
    github                TEXT,
    cv                    TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users (stripe_customer_id);
CREATE TABLE IF NOT EXISTS resource_group_shares (
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    group_id  TEXT NOT NULL,
    shared_by     TEXT NOT NULL,
    shared_at     TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_group_share_group ON resource_group_shares(group_id, resource_type);
CREATE INDEX IF NOT EXISTS idx_group_share_resource  ON resource_group_shares(resource_type, resource_id);
CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_active   SMALLINT NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    username     TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member',
    permissions  TEXT NOT NULL DEFAULT '{}',
    joined_at    TEXT NOT NULL,
    PRIMARY KEY (group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(username);
CREATE TABLE IF NOT EXISTS token_daily (
    day      TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    tokens   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_token_daily_owner ON token_daily(owner_id, day DESC);
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
CREATE TABLE IF NOT EXISTS subscriptions (
    id                     TEXT PRIMARY KEY,
    username               TEXT NOT NULL,
    stripe_customer_id     TEXT NOT NULL,
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    tier                   TEXT NOT NULL,
    seats                  INTEGER NOT NULL DEFAULT 1,
    self_hosted            SMALLINT NOT NULL DEFAULT 0,
    interval               TEXT NOT NULL,
    amount_cents           INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL,
    current_period_end     TEXT,
    cancel_at_period_end   SMALLINT NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_username ON subscriptions(username);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(stripe_customer_id);
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
CREATE TABLE IF NOT EXISTS stripe_events (
    stripe_event_id TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    processed_at    TEXT NOT NULL,
    payload         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_logs (
    id       BIGSERIAL PRIMARY KEY,
    ts       DOUBLE PRECISION NOT NULL,
    date     TEXT    NOT NULL,
    time     TEXT    NOT NULL,
    ip       TEXT    NOT NULL DEFAULT '-',
    username TEXT    NOT NULL DEFAULT '-',
    level    TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'BE',
    summary  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_date     ON app_logs(date);
CREATE INDEX IF NOT EXISTS idx_al_level    ON app_logs(level);
CREATE INDEX IF NOT EXISTS idx_al_username ON app_logs(username);
CREATE INDEX IF NOT EXISTS idx_al_ip       ON app_logs(ip);
CREATE INDEX IF NOT EXISTS idx_al_source   ON app_logs(source);
CREATE TABLE IF NOT EXISTS user_agent_preferences (
    username      TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    connection_id TEXT,
    updated_at    TEXT NOT NULL DEFAULT (NOW()::TEXT),
    PRIMARY KEY (username, agent_id)
);
CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pat_hash ON personal_access_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_pat_user ON personal_access_tokens(username, created_at DESC);
CREATE TABLE IF NOT EXISTS vscode_auth_codes (
    code_hash  TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    state      TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_versions (
    id            TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    snapshot      TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT 'save',
    created_at    TEXT NOT NULL,
    UNIQUE(resource_type, resource_id, owner_id, version)
);
CREATE INDEX IF NOT EXISTS idx_resource_versions_lookup
    ON resource_versions(resource_type, resource_id, owner_id, version DESC);
CREATE TABLE IF NOT EXISTS agent_workflows (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition  TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'private',
    labels      TEXT NOT NULL DEFAULT '["private"]',
    is_active   SMALLINT NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY(id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_workflows_owner
    ON agent_workflows(owner_id, updated_at DESC);
"""
