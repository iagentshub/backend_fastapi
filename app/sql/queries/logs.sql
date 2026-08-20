-- Consultas de app/api/routes/logs.py.

-- name: daily_summary
SELECT date, COUNT(*) as lines, SUM(CASE WHEN level='WARNING' AND source='BE' THEN 1 ELSE 0 END) as be_warnings, SUM(CASE WHEN level='ERROR' AND source='BE' THEN 1 ELSE 0 END) as be_errors, SUM(CASE WHEN level='WARNING' AND source='FE' THEN 1 ELSE 0 END) as fe_warnings, SUM(CASE WHEN level='ERROR' AND source='FE' THEN 1 ELSE 0 END) as fe_errors, SUM(CASE WHEN level='WARNING' THEN 1 ELSE 0 END) as warnings, SUM(CASE WHEN level='ERROR' THEN 1 ELSE 0 END) as errors, SUM(CASE WHEN category='AUDIT' THEN 1 ELSE 0 END) as audits
FROM app_logs
GROUP BY date
ORDER BY date DESC;

-- name: count_expired
SELECT COUNT(*)
FROM app_logs
WHERE (category <> 'AUDIT' AND date < ?)
   OR (category = 'AUDIT' AND date < ?);

-- name: delete_expired
DELETE FROM app_logs
WHERE (category <> 'AUDIT' AND date < ?)
   OR (category = 'AUDIT' AND date < ?);
