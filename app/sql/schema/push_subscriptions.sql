-- sensitive-columns: endpoint, p256dh, auth
-- Las claves de cifrado del navegador (RFC 8291) y su endpoint. Estas tres
-- juntas son exactamente lo que hace falta para mandarle notificaciones a
-- alguien haciéndose pasar por la aplicación, así que no son como los hashes
-- de arriba: se pueden usar tal cual, no hay que reconstruir nada.
-- A dónde empujar un aviso. Una fila por navegador o dispositivo, no por
-- usuario: la misma persona tiene el portátil, el móvil y el trabajo, y espera
-- que le salte en los tres.
--
-- `kind` existe desde el primer día aunque hoy solo se escriba 'webpush'. Los
-- tres canales que puede necesitar este producto —Web Push, FCM en Android
-- nativo y APNs en iOS nativo— se distinguen en el emisor, no en el esquema:
-- los tres son «un destino opaco al que mandar un JSON». Cuando se publiquen
-- las apps, FCM y APNs son filas con otro `kind` y una rama en `push.py`, no
-- una tabla nueva ni un cambio en los productores de avisos.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'webpush',
    -- La URL del servicio push del navegador, o el token del dispositivo en
    -- FCM/APNs. Es UNIQUE porque el navegador reutiliza la misma al volver a
    -- suscribirse: sin esto, cada recarga de la app añadiría un duplicado y el
    -- usuario recibiría el mismo aviso N veces.
    endpoint     TEXT NOT NULL UNIQUE,
    -- Claves de cifrado del navegador (RFC 8291). Vacías en FCM/APNs, que
    -- cifran por su cuenta.
    p256dh       TEXT NOT NULL DEFAULT '',
    auth         TEXT NOT NULL DEFAULT '',
    -- Para que el usuario reconozca cuál es cuál si algún día listamos sus
    -- dispositivos, como ya hace la pantalla de sesiones.
    user_agent   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_sent_at TEXT
);
-- ponytail: las suscripciones muertas no se barren por antigüedad. No hace
-- falta: el servicio push responde 404 o 410 cuando el navegador ya la tiró, y
-- `push.py` la borra en ese momento. Es la propia entrega la que limpia.

-- El envío siempre pregunta «las de este usuario».
CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subscriptions(user_id);
