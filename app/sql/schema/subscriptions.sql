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
