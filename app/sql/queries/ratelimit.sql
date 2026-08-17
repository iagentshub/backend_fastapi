-- Consultas de app/middleware/ratelimit.py.

-- Cuota compartida entre workers en un solo viaje: el UPSERT reinicia la
-- ventana si la que hay ya expiró y, si no, incrementa. Hacerlo en dos
-- sentencias abriría una carrera entre procesos justo en el borde de la
-- ventana, que es cuando llegan las ráfagas.
-- name: consume_window
INSERT INTO rate_limit_windows(limiter_key, window_start, request_count)
VALUES (?, ?, 1)
ON CONFLICT(limiter_key) DO UPDATE SET
    window_start = CASE
        WHEN rate_limit_windows.window_start <= ? THEN excluded.window_start
        ELSE rate_limit_windows.window_start
    END,
    request_count = CASE
        WHEN rate_limit_windows.window_start <= ? THEN 1
        ELSE rate_limit_windows.request_count + 1
    END
RETURNING request_count, window_start;
