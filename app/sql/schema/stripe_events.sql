CREATE TABLE IF NOT EXISTS stripe_events (
    stripe_event_id TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    processed_at    TEXT NOT NULL,
    payload         TEXT NOT NULL
);

-- Era la única tabla del esquema de la que no se borraba nunca nada: crecía con
-- todo lo que Stripe mandase, no con lo que se procesa, y no la alcanza el
-- borrado RGPD porque no tiene columna de dueño —solo el `customer` dentro del
-- JSON—. La purga barre por `processed_at`, y sin este índice barrer por fecha
-- recorrería la tabla entera, que es justo lo que la purga viene a evitar.
CREATE INDEX IF NOT EXISTS idx_stripe_events_processed
    ON stripe_events(processed_at);
