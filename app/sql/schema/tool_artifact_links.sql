CREATE TABLE IF NOT EXISTS tool_artifact_links (
    tool_id  TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    sha256   TEXT NOT NULL REFERENCES tool_artifacts(sha256),
    PRIMARY KEY (tool_id, owner_id)
);
