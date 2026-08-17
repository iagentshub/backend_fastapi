-- Consultas de app/api/routes/admin/stats.py.

-- name: pg_table_stats
-- engine: pg
SELECT tablename AS name, (
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_name=tablename AND table_schema='public') AS col_count, COALESCE(n_live_tup, 0) AS rows, pg_total_relation_size(quote_ident(tablename)) AS size_bytes
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;

-- name: sqlite_table_names
-- engine: sqlite
SELECT name
FROM sqlite_master
WHERE type='table'
ORDER BY name;

-- name: sqlite_table_size
-- engine: sqlite
SELECT SUM(payload)
FROM dbstat
WHERE name=?;

-- name: pg_column_names
-- engine: pg
SELECT column_name
FROM information_schema.columns
WHERE table_name=? AND table_schema='public'
ORDER BY ordinal_position;

-- name: user_counts
SELECT COUNT(*), SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END), SUM(CASE WHEN is_verified=1 THEN 1 ELSE 0 END)
FROM users;

-- name: connection_totals
SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0)
FROM connections;

-- name: count_knowledge
SELECT COUNT(*)
FROM knowledge_items;

-- name: count_conversations
SELECT COUNT(*)
FROM conversations;

-- name: count_workflows
SELECT COUNT(*)
FROM agent_workflows;

-- name: tokens_per_day
SELECT day, SUM(tokens)
FROM token_daily
WHERE day >= ?
GROUP BY day
ORDER BY day ASC;

-- name: seed_token_daily_pg
INSERT INTO token_daily (day, owner_id, tokens)
SELECT ?, owner_id, tokens_in + tokens_out
FROM connections
WHERE tokens_in + tokens_out > 0
ON CONFLICT (day, owner_id) DO NOTHING;

-- name: seed_token_daily_sqlite
-- engine: sqlite
INSERT OR IGNORE INTO token_daily (day, owner_id, tokens)
SELECT ?, owner_id, tokens_in + tokens_out
FROM connections
WHERE tokens_in + tokens_out > 0;

-- name: logs_of_day
SELECT level, summary
FROM app_logs
WHERE source='BE' AND date = ?;
