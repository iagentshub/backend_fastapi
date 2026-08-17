-- Consultas de app/api/routes/logs.py.

-- name: daily_summary
SELECT date, COUNT(*) as lines, SUM(CASE WHEN level='WARNING' AND source='BE' THEN 1 ELSE 0 END) as be_warnings, SUM(CASE WHEN level='ERROR' AND source='BE' THEN 1 ELSE 0 END) as be_errors, SUM(CASE WHEN level='WARNING' AND source='FE' THEN 1 ELSE 0 END) as fe_warnings, SUM(CASE WHEN level='ERROR' AND source='FE' THEN 1 ELSE 0 END) as fe_errors, SUM(CASE WHEN level='WARNING' THEN 1 ELSE 0 END) as warnings, SUM(CASE WHEN level='ERROR' THEN 1 ELSE 0 END) as errors
FROM app_logs
GROUP BY date
ORDER BY date DESC;

-- name: admin_preferences
SELECT preferences
FROM users
WHERE role = 'admin'
LIMIT 1;

-- name: count_before
SELECT COUNT(*)
FROM app_logs
WHERE date < ?;

-- name: delete_before
DELETE FROM app_logs
WHERE date < ?;
