CREATE TABLE IF NOT EXISTS rate_limit_windows (
    limiter_key TEXT PRIMARY KEY,
    window_start @FLOAT@ NOT NULL,
    request_count INTEGER NOT NULL
);
