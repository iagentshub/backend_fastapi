-- Consultas de app/middleware/ratelimit.py.

-- Cuota compartida entre workers en un solo viaje: el UPSERT reinicia la
-- ventana si la que hay ya expiró y, si no, incrementa. Hacerlo en dos
-- sentencias abriría una carrera entre procesos justo en el borde de la
-- ventana, que es cuando llegan las ráfagas.
-- name: consume_window_weighted
INSERT INTO rate_limit_windows(limiter_key, window_start, request_count)
VALUES (?, ?, ?)
ON CONFLICT(limiter_key) DO UPDATE SET
    window_start = CASE
        WHEN rate_limit_windows.window_start <= ? THEN excluded.window_start
        ELSE rate_limit_windows.window_start
    END,
    request_count = CASE
        WHEN rate_limit_windows.window_start <= ? THEN excluded.request_count
        ELSE rate_limit_windows.request_count + ?
    END
RETURNING request_count, window_start;

-- Purga del bucle de mantenimiento: una ventana cuyo inicio quedó fuera del
-- horizonte ya no cuenta para nadie —la siguiente petición la reiniciaría—,
-- así que borrarla no devuelve cuota a nadie.
-- name: count_expired
SELECT COUNT(*) FROM rate_limit_windows WHERE window_start < ?;

-- name: purge_expired
DELETE FROM rate_limit_windows WHERE window_start < ?;
