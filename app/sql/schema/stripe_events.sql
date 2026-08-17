CREATE TABLE IF NOT EXISTS stripe_events (
    stripe_event_id TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    processed_at    TEXT NOT NULL,
    payload         TEXT NOT NULL
);
