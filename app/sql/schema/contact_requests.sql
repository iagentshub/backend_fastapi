-- Peticiones del formulario de contacto de la web pública (planes sin checkout
-- directo: Tropa, Legión y formación). Es la única tabla que escribe alguien
-- sin sesión, así que su defensa no es el rango sino el limiter por IP y la
-- longitud máxima de cada campo, que valida pydantic antes de llegar aquí.
--
-- Se guarda además de enviar el correo, y no en lugar de: con SMTP sin
-- configurar —o caído— el aviso se pierde en silencio, y un lead perdido no
-- se recupera. La fila es la copia que siempre queda.
CREATE TABLE IF NOT EXISTS contact_requests (
    id         @SERIAL@,
    created_at TEXT NOT NULL,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT '',
    ip         TEXT
);
-- ponytail: no hay purga. La fila guarda email e IP de alguien que no tiene
-- cuenta, así que el plazo de conservación es una decisión que pertenece a la
-- política de privacidad, hoy con el plazo sin rellenar. Cuando ese número
-- exista, esta tabla se barre desde el bucle de purga del RGPD como las demás.

-- El listado del admin ordena por fecha descendente y no filtra por nada más.
CREATE INDEX IF NOT EXISTS idx_contact_requests_fecha ON contact_requests(created_at DESC);
