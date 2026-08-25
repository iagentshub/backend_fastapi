CREATE TABLE IF NOT EXISTS tool_version_artifacts (
    version_id TEXT PRIMARY KEY REFERENCES resource_versions(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL REFERENCES tool_artifacts(sha256)
);
