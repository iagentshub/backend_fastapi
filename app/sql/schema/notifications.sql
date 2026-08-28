-- Avisos por usuario: lo que alimenta la campana de la app y el correo que
-- sale a la vez. Hasta ahora una invitación a un grupo era solo una fila en
-- `group_invitations` y el invitado solo se enteraba si entraba a mirar.
--
-- El texto NO se guarda renderizado, a propósito: la fila lleva `kind` y un
-- `data` JSON con los huecos (`actor`, `group`...), y cada canal lo pinta en el
-- idioma que toca —Flutter con la clave `notif_<kind>` de `nav.json`, el
-- correo con `_TEXTOS` de
-- `app/services/email.py`—. Guardar la frase ya escrita congelaría el idioma
-- del instante del evento, y esta instalación sirve es/en.
--
-- Tampoco hay columna de destino: el `kind` lo resuelve un switch en el cliente
-- que llama a los helpers de `AppRouter`, que es como se navega en esa app.
CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    -- El id interno del destinatario, no su username público. La columna
    -- homóloga de `group_invitations` se llama `username` y guarda un id --su
    -- SQL hace `JOIN users u ON u.id = wi.username`--, y el nombre de aquí
    -- evita repetir esa confusión.
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    data       TEXT NOT NULL DEFAULT '{}',
    read_at    TEXT,
    created_at TEXT NOT NULL
);
-- ponytail: sin purga por antigüedad. Las filas se entregan y se borran con su
-- usuario (`queries/gdpr_export:notifications` y `queries/gdpr:delete_notifications`),
-- pero nadie barre las viejas de una cuenta viva. El día que la tabla pese, va
-- al bucle de purga como las demás.

-- El listado siempre es «las mías, de la más reciente a la más antigua», y el
-- contador del badge filtra por `read_at IS NULL` sobre ese mismo prefijo.
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications(user_id, read_at, created_at DESC);
