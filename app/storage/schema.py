"""DDL base para SQLite y PostgreSQL.

Una sola definición, dos dialectos. Antes eran dos constantes de 331 líneas
cada una, idénticas al 91%: mismas 26 tablas, mismas columnas, mismos 33
índices, y solo 29 líneas de diferencia — siempre por lo mismo (el tipo de los
booleanos, el autoincremento, el flotante y una expresión de fecha por
defecto). El coste no era el tamaño sino que cada cambio había que escribirlo
dos veces sin que nada lo comprobara: la columna `labels` de `knowledge_items`
llegó a estar añadida a mano en los dos bloques, y una divergencia en el de
PostgreSQL no la detecta la suite, que corre siempre en SQLite.

`tests/storage/test_schema_dialectos.py` fija el resultado: el DDL generado es
el mismo que había, salvo la alineación de espacios dentro de `users`, que SQL
ignora.

Los marcadores son `@NOMBRE@` y no `{NOMBRE}` porque el DDL lleva llaves
propias y `str.format` las interpretaría.

Ojo al tocar los dialectos: `migrate_schema` parte el DDL de PostgreSQL por
`";"` (ver db.py), así que ninguna sustitución puede meter un punto y coma
dentro de un literal.

Las migraciones incrementales permanecen en :mod:`app.storage.db`.
"""

from __future__ import annotations

_DIALECTOS: dict[str, dict[str, str]] = {
    "sqlite": {
        "BOOL": "INTEGER",
        "SERIAL": "INTEGER PRIMARY KEY AUTOINCREMENT",
        # El relleno mantiene la alineación de la columna; SQL lo ignora, pero
        # así el DDL generado se lee igual que cuando estaba escrito a mano.
        "FLOAT": "REAL   ",
        "NOW": "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
    },
    "pg": {
        "BOOL": "SMALLINT",
        "SERIAL": "BIGSERIAL PRIMARY KEY",
        "FLOAT": "DOUBLE PRECISION",
        "NOW": "(NOW()::TEXT)",
    },
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    is_active   @BOOL@ NOT NULL DEFAULT 1,
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
    category    TEXT CHECK (category IS NULL OR category IN ('ai','messaging','notes','productivity','dev','security','media','data','company')),
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id, scope, updated_at DESC);
CREATE TABLE IF NOT EXISTS prompts (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    alias       TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_prompts_owner ON prompts(owner_id, scope, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_alias_owner ON prompts(owner_id, alias);
CREATE TABLE IF NOT EXISTS tools (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '__public__',
    name        TEXT NOT NULL DEFAULT '',
    language    TEXT NOT NULL DEFAULT 'python' CHECK (language IN ('python','shell','cpp')),
    scope       TEXT NOT NULL DEFAULT 'private',
    data        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    binary_b64         TEXT,
    binary_filename    TEXT,
    binary_size        INTEGER,
    binary_uploaded_at TEXT,
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_tools_owner ON tools(owner_id, scope, updated_at DESC);
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
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    data        TEXT NOT NULL,
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id, provider);
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
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
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
    labels     TEXT NOT NULL DEFAULT '["private"]',
    is_active  @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner
    ON knowledge_items(owner_id, type, created_at DESC);
CREATE TABLE IF NOT EXISTS official_packages (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    repository_url      TEXT NOT NULL UNIQUE,
    repository_owner    TEXT NOT NULL,
    repository_name     TEXT NOT NULL,
    tracking_mode       TEXT NOT NULL DEFAULT 'release',
    tracking_ref        TEXT NOT NULL DEFAULT 'main',
    license             TEXT NOT NULL DEFAULT '',
    published_version   TEXT,
    latest_checked_at   TEXT,
    last_sync_error     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS official_package_versions (
    package_id          TEXT NOT NULL REFERENCES official_packages(id) ON DELETE CASCADE,
    version             TEXT NOT NULL,
    commit_sha          TEXT NOT NULL,
    status              TEXT NOT NULL,
    manifest            TEXT NOT NULL DEFAULT '{}',
    validation_errors   TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    reviewed_at         TEXT,
    reviewed_by         TEXT,
    PRIMARY KEY (package_id, version)
);
CREATE INDEX IF NOT EXISTS idx_official_versions_status
    ON official_package_versions(status, created_at DESC);
CREATE TABLE IF NOT EXISTS official_package_components (
    package_id          TEXT NOT NULL,
    version             TEXT NOT NULL,
    component_id        TEXT NOT NULL,
    component_type      TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    source_path         TEXT NOT NULL,
    content             TEXT NOT NULL DEFAULT '',
    files               TEXT NOT NULL DEFAULT '{}',
    targets             TEXT NOT NULL DEFAULT '[]',
    labels              TEXT NOT NULL DEFAULT '["official"]',
    dependencies        TEXT NOT NULL DEFAULT '[]',
    content_hash        TEXT NOT NULL,
    PRIMARY KEY (package_id, version, component_id),
    FOREIGN KEY (package_id, version)
        REFERENCES official_package_versions(package_id, version) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS official_package_copies (
    id                  TEXT PRIMARY KEY,
    owner_id            TEXT NOT NULL,
    package_id          TEXT NOT NULL,
    source_version      TEXT NOT NULL,
    component_id        TEXT NOT NULL,
    resource_type       TEXT NOT NULL,
    resource_id         TEXT,
    name                TEXT NOT NULL,
    content             TEXT NOT NULL DEFAULT '',
    source_content_hash TEXT NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'copy',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_official_copies_owner
    ON official_package_copies(owner_id, package_id, created_at DESC);
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
    is_active             @BOOL@ NOT NULL DEFAULT 1,
    is_verified           @BOOL@ NOT NULL DEFAULT 1,
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
    is_email_public       @BOOL@ NOT NULL DEFAULT 0,
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
    is_active   @BOOL@ NOT NULL DEFAULT 1
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
    self_hosted            @BOOL@ NOT NULL DEFAULT 0,
    interval               TEXT NOT NULL,
    amount_cents           INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL,
    current_period_end     TEXT,
    cancel_at_period_end   @BOOL@ NOT NULL DEFAULT 0,
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
    id       @SERIAL@,
    ts       @FLOAT@ NOT NULL,
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
CREATE TABLE IF NOT EXISTS rate_limit_windows (
    limiter_key TEXT PRIMARY KEY,
    window_start @FLOAT@ NOT NULL,
    request_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS user_agent_preferences (
    username      TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    connection_id TEXT,
    updated_at    TEXT NOT NULL DEFAULT @NOW@,
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
    is_active   @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY(id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_workflows_owner
    ON agent_workflows(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    started_by      TEXT NOT NULL,
    group_id        TEXT NOT NULL,
    workflow_name   TEXT NOT NULL,
    definition      TEXT NOT NULL,
    agents          TEXT NOT NULL DEFAULT '[]',
    input           TEXT NOT NULL,
    status          TEXT NOT NULL,
    completed_steps INTEGER NOT NULL DEFAULT 0,
    total_steps     INTEGER NOT NULL DEFAULT 0,
    active_node_id  TEXT,
    final_output    TEXT,
    error           TEXT,
    last_sequence   INTEGER NOT NULL DEFAULT 0,
    heartbeat_at    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_user
    ON workflow_runs(started_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
    ON workflow_runs(status, heartbeat_at);
CREATE TABLE IF NOT EXISTS workflow_run_events (
    run_id      TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY(run_id, sequence),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_workflow_run_events_run
    ON workflow_run_events(run_id, sequence);
CREATE TABLE IF NOT EXISTS llm_orchestrations (
    id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL,
    labels TEXT NOT NULL DEFAULT '["private"]',
    is_active @BOOL@ NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_llm_orchestrations_owner
    ON llm_orchestrations(owner_id, updated_at DESC);
"""


def schema_for(dialecto: str) -> str:
    """DDL completo para ``sqlite`` o ``pg``."""
    try:
        sustituciones = _DIALECTOS[dialecto]
    except KeyError:
        raise ValueError(f"Dialecto de esquema desconocido: {dialecto!r}") from None
    ddl = _SCHEMA
    for marcador, valor in sustituciones.items():
        ddl = ddl.replace(f"@{marcador}@", valor)
    return ddl


SCHEMA_SQLITE = schema_for("sqlite")
SCHEMA_PG = schema_for("pg")
