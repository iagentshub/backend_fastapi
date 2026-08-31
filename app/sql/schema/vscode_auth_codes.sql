-- sensitive-columns: code_hash
-- El código de emparejamiento de la extensión.
CREATE TABLE IF NOT EXISTS vscode_auth_codes (
    code_hash  TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    state      TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
