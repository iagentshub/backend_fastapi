-- Invitados: usuarios efímeros con `role = 'guest'`.
--
-- El invitado tiene fila en `users` como cualquiera —es lo que le permite usar
-- el mismo almacenamiento que un usuario registrado, en vez de un dict del
-- proceso que se perdía al cambiar de worker— y se borra entero con
-- `purge_user_data` al cerrar sesión o al quedarse sin sesiones vivas.
-- Ver docs/adr/012-el-invitado-es-un-usuario-efimero.md.

-- name: count_guests
SELECT COUNT(*)
FROM users
WHERE role = 'guest';

-- name: expired_guests
-- Invitados abandonados: los que ya no tienen ninguna sesión viva y llevan
-- creados más que el margen de gracia. El margen existe porque entre el INSERT
-- del usuario y el de su sesión hay una ventana en la que un invitado recién
-- creado no tiene sesión todavía, y sin él la purga se lo llevaría por delante.
SELECT id
FROM users
WHERE role = 'guest'
  AND created_at < ?
  AND NOT EXISTS (
      SELECT 1
      FROM sessions
      WHERE sessions.user_id = users.id
        AND sessions.revoked_at IS NULL
        AND sessions.expires_at > ?
  );
