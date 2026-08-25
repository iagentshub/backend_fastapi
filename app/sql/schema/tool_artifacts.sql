CREATE TABLE IF NOT EXISTS tool_artifacts (
    sha256      TEXT PRIMARY KEY,
    binary_data @BLOB@ NOT NULL,
    size        INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
