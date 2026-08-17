# 008 · Sesiones revocables: access corto, refresh rotatorio y una tabla que manda

- **Fecha**: 2026-08-18
- **Estado**: aceptada
- **Afecta a**: `backend_fastapi/app/storage/sessions.py`,
  `backend_fastapi/app/sql/schema/sessions.sql`,
  `backend_fastapi/app/sql/queries/sessions.sql`,
  `backend_fastapi/app/auth/sessions.py`,
  `backend_fastapi/app/auth/passwords.py`,
  `backend_fastapi/app/auth/cookies.py`,
  `backend_fastapi/app/auth/auth.py`,
  `backend_fastapi/app/api/routes/auth/dependencies.py`,
  `backend_fastapi/app/api/routes/auth/login.py`,
  `backend_fastapi/app/config/session.py`,
  `app_flutter/lib/core/network/api_client.dart`,
  `app_flutter/lib/features/profile/…`

## Contexto

La sesión web era un JWT HS256 de 12 horas y nada más. Un JWT no se puede
retirar: mientras la firma cuadre y no haya pasado su `exp`, vale. El único
mecanismo de invalidación era comparar el `iat` del token con
`password_changed_at`, que cubre exactamente un caso —«he cambiado la
contraseña»— y deja fuera todos los demás:

- **Cerrar sesión no cerraba nada.** `POST /api/auth/logout` borraba las
  cookies del navegador que las tenía. Quien hubiera copiado el token seguía
  autenticado hasta 12 horas después, y no había forma de enterarse ni de
  impedirlo.
- **No se podía ver ni cortar una sesión ajena.** Ni listado de sesiones
  abiertas, ni «cerrar sesión en todos los dispositivos». Ante la sospecha de
  un acceso, la única acción disponible era cambiar la contraseña.
- **Doce horas es mucho** para una credencial que no se puede retirar. La
  combinación habitual es access corto y refresh revocable.

Tres cosas que el informe daba por rotas ya funcionaban, y conviene dejarlo
escrito para no «arreglarlas» dos veces: expulsar a alguien de un grupo sí corta
su acceso al grupo (`_resolve_group` revalida la pertenencia en cada request y
cae al espacio personal), y desactivar la cuenta o cambiar el rol también se
aplican, con el retraso de la caché de 60 s de `_get_user_auth_state`. `iss` y
`aud` ya se emitían y se validaban.

## Decisión

Una tabla `sessions`, un claim `sid` en el access token, y una consulta por
request autenticado que contrasta el uno con la otra.

### El access token deja de ser autosuficiente

`create_token` mete `sid` en el payload y `_identify` llama a
`_assert_session_live()` antes de devolver el principal. Un token cuya fila
está revocada, borrada o caducada da 401 `session_revoked`, aunque su firma sea
perfecta y su `exp` esté lejos. Eso es lo que convierte «cerrar sesión» en un
hecho y no en una apariencia.

**Sin caché, deliberadamente.** El estado de la cuenta se cachea 60 s
(`_get_user_auth_state`) y es razonable: una cuenta suspendida puede tardar un
minuto. La revocación no puede: el retraso que introduciría una caché es
exactamente el problema que esto viene a resolver, y con varios workers sería
peor —el logout se aplicaría de inmediato solo en el proceso que lo atendió, y
el usuario vería su sesión viva o muerta según a qué worker cayera cada
petición. Es una consulta por PK sobre una tabla pequeña; el coste está medido
contra el de dar por cerrada una sesión que no lo está.

### Access de 30 minutos, refresh de la vida de la sesión

`GAIA_ACCESS_EXPIRE_MINUTES` (30 por defecto) mide el access.
`GAIA_JWT_EXPIRE_HOURS` conserva su nombre y su valor (12) pero pasa a medir la
**sesión**: una instalación que ya lo tuviera puesto sigue teniendo sesiones de
la duración que pidió y gana el access corto sin tocar nada.

La rotación mueve esa caducidad hacia delante en cada renovación, así que son
12 horas de inactividad, no desde el login. Sin eso, un access corto obligaría
a volver a entrar a media jornada de trabajo.

### El refresh rota y el reuso tumba la sesión

Cada canje emite un refresh nuevo y guarda el anterior en `prev_refresh_hash`.
Si alguien presenta un refresh ya rotado, es que dos clientes lo tienen: uno de
los dos lo robó y no hay forma de saber cuál. La sesión entera cae. Es la
respuesta estándar a la detección de reuso, y es la razón de que la columna
exista: sin ella, el ladrón y la víctima se irían turnando la renovación
indefinidamente sin que nada lo notase.

De ambos hashes se guarda el SHA-256, nunca el token, igual que en
`personal_access_tokens`. Y la cookie del refresh se acota a `path=/api/auth`:
es la credencial de largo recorrido y no tiene por qué viajar en las otras ~450
rutas.

### La cookie del access dura lo que la sesión

Parece contradictorio, y es a propósito. Si el navegador borrase `ga_token` al
expirar el JWT, la petición llegaría sin credencial y el 401 sería
indistinguible de «este usuario nunca entró». Con la cookie presente, el token
llega caducado, el backend lo dice, y el cliente sabe que le toca renovar en vez
de mandar al login. Quien impone la caducidad es el `exp` firmado, que el
navegador no puede tocar.

Eso también mantiene en pie el anti-CSRF: `ga_csrf` es `HMAC(secreto,
ga_token)` (ver ADR 006) y `POST /api/auth/refresh` es una mutación como
cualquier otra. Con la cookie del access todavía en su sitio, el token
sincronizador sigue cuadrando aunque el JWT haya caducado.

### Quién revoca

- **logout** — la sesión propia. Sin `require_auth`: cerrar sesión tiene que
  funcionar también con el access caducado, que es justo cuando más se intenta.
- **`DELETE /api/auth/sessions/{id}`** — una concreta, del propio usuario.
- **`DELETE /api/auth/sessions`** — todas menos la actual. Conservarla es lo que
  hace la acción usable: quien sospecha de un acceso ajeno no debería quedarse
  fuera justo mientras intenta echar al otro.
- **cambio de contraseña** — todas, desde `_touch_password_changed_at`, que es
  el punto por el que pasan los tres caminos (perfil, token de recuperación,
  reseteo por admin). `password_changed_at` por sí solo no bastaba: invalida el
  access, que se contrasta contra él, pero el refresh no pasa por ahí y la
  sesión robada se habría renovado tan tranquila.
- **desactivar la cuenta** — todas, ahora y no cuando caduque la caché de 60 s.
- **reuso de refresh** — la sesión afectada.
- **borrado RGPD** — las filas se van con el resto.

**El cambio de rol no revoca**, y es una decisión, no un olvido: el rol se lee
de la fila del usuario en cada request, así que ya se aplica solo; expulsar a
alguien de su sesión por haberle dado permisos sería desconcertante.

### Cambiar de grupo no abre una sesión

`POST /api/groups/switch/{id}` reemite el access con otro `gid` y el mismo
`sid`, sin tocar el refresh (`reissue_access`). Si abriera una sesión nueva, la
lista del perfil acumularía una fila por cada cambio de grupo y ninguna sería la
que el usuario reconoce.

## Consecuencias

- Una consulta más por request autenticado con cookie. Es por clave primaria y
  la tabla se purga sola; `last_seen_at` solo se escribe una vez cada 5 minutos
  (`ponytail:` en `storage/sessions.py`) para no convertir cada lectura en una
  escritura.
- **Los PAT no pasan por aquí.** `Authorization: Bearer` resuelve contra
  `personal_access_tokens`, que ya tenía su propia revocación. La extensión de
  VS Code no se entera de este cambio.
- **Flutter es el único cliente afectado.** React sirve páginas públicas y no
  hace peticiones autenticadas propias. `ApiClient` renueva ante un 401 y
  reintenta una vez, con un cerrojo para que N peticiones en vuelo no disparen
  N renovaciones —que con la rotación se pisarían entre sí y acabarían
  pareciendo un reuso.
- **Los tokens sin `sid` se aceptan.** Son los emitidos antes de que la tabla
  existiera; rechazarlos habría echado de golpe a todos los usuarios con sesión
  abierta durante el despliegue. Están firmados y son auténticos, y la ventana
  se cierra sola cuando caducan. `test_un_token_sin_sesion_sigue_valiendo` fija
  ese comportamiento: el día que se quiera cerrar, ese es el test que cambia.
- Queda pendiente lo mismo que en el resto del backend: las tablas indexadas
  por `username` en vez de por id siguen fuera de la cobertura automática del
  RGPD. `sessions` usa `user_id` y sí entra.
