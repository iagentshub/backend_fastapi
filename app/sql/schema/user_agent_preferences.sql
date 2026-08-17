CREATE TABLE IF NOT EXISTS user_agent_preferences (
    username      TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    connection_id TEXT,
    updated_at    TEXT NOT NULL DEFAULT @NOW@,
    PRIMARY KEY (username, agent_id)
);
