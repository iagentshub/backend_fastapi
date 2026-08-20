# 012 · El invitado es un usuario efímero, no un dict en memoria

- **Fecha**: 2026-08-20
- **Estado**: aceptada; sustituye la parte de invitados de la
  [001](001-estado-en-memoria-con-multiples-workers.md)
- **Afecta a**: `app/storage/guest.py`, `app/auth/gdpr.py`,
  `app/api/routes/auth/session.py`, `app/api/routes/auth/dependencies.py`,
  `app/api/app.py`, `main.py`, `app/sql/queries/guest.sql`, y los 22 ficheros
  que tenían una rama `is_guest(...)`

## Contexto

La sesión de invitado vivía en `_sessions`, un `dict` del proceso, con seis
listas dentro: conexiones, agentes, skills, knowledge, prompts y memoria.

Eso traía tres problemas de distinta gravedad.

**El del punto 22**: uvicorn arranca `GAIA_WORKERS` procesos independientes y
el proxy no tiene afinidad de sesión. Dos peticiones del mismo invitado caen en
workers distintos, `get_session` crea una vacía en el segundo, y el agente que
el invitado acababa de crear desaparece. Sin error, sin log, solo la demo
comportándose como si el usuario no hubiera hecho nada. El tope `MAX_SESSIONS`
era igualmente por proceso, así que el límite real era `workers × 200`. Estaba
avisado en el arranque con un `flog.warning` desde el 001, que es media
solución: convertía un fallo silencioso en un fallo anunciado.

**El del contrato**: el invitado no podía hacer lo mismo que un usuario. Cada
endpoint que quisiera admitirlo tenía que escribirse **dos veces** —una contra
la `GuestSession`, otra contra el almacenamiento real—, así que en la práctica
solo se escribieron las que costaba poco: tools, workflows, versiones y los
chats guardados nunca llegaron, y `POST /api/chats/{agent_id}` tenía un 403
explícito diciendo que los invitados no guardan conversaciones. Eran 107 ramas
`is_guest(...)` repartidas por 22 ficheros, y cada recurso nuevo sumaba las
suyas o dejaba al invitado fuera.

**El del borrado**: cerrar sesión no borraba nada. El logout revocaba la fila de
`sessions` y limpiaba las cookies, pero la `GuestSession` seguía en el `dict`
hasta agotar su TTL de 12 h, ocupando un hueco del tope. El único borrado real
era el que nadie quería: el cambio de worker.

## Decisión

**El invitado es un usuario de verdad, con fecha de caducidad.** Una fila en
`users` con `role='guest'`, `id` y `username` iguales a `guest:<id>` —el mismo
identificador que ya viajaba en el `sub` del JWT, así que `is_guest()` sigue
siendo una comprobación de prefijo sin consultar nada— y un email sintético en
`.invalid`, que es un TLD reservado y por tanto no colisiona ni es enrutable.

A partir de ahí no hay caso especial: usa el mismo almacenamiento, las mismas
consultas y el mismo espacio personal (`group_id == user_id`) que cualquiera.
Las 107 ramas desaparecen.

Lo que lo distingue no es dónde guarda, sino **cuánto dura**:

- **Al cerrar sesión** se le aplica `purge_user_data`, la rutina del RGPD, que
  ya borra todo recurso con dueño y la propia fila de `users`. Para el invitado
  no es una cortesía legal: es su contrato.
- **Por abandono**, `purge_expired_guests()` se lleva a los que no tienen
  ninguna sesión viva pasado un margen de gracia. Cuelga del bucle de purga del
  RGPD que ya existía —misma escoba, mismo barrido— en vez de estrenar un
  `asyncio.sleep` más por worker.

El alta **solo purga al topar**. La primera versión purgaba siempre —así el
tope nunca lo consumen los muertos que el bucle aún no ha barrido— y eso ponía
un borrado RGPD por invitado abandonado en la primera petición de la demo:
medido, 146 ms con 10 abandonados y 608 ms con 150, justo en la pantalla que el
informe llamaba «la primera impresión del producto». Comprobar el hueco cuesta
un `COUNT`, y la limpieza la paga solo quien se encuentra la puerta cerrada,
que es a quien de otro modo le tocaría el 503. Tras el cambio, esas mismas
altas tardan 1,9-3,1 ms.

El criterio de expiración es **«sin sesión viva»**, no un TTL desde el alta.
Con un TTL, un invitado que sigue trabajando pierde su trabajo por debajo al
cumplirse la hora; con este, quien renueva su sesión sigue, y quien cierra la
pestaña sin pasar por logout se limpia en la siguiente pasada. El margen de
gracia existe porque entre el `INSERT` del usuario y el de su sesión hay una
ventana en la que el invitado recién creado todavía no tiene ninguna: sin él, la
purga se lo llevaría en mitad de su primera petición.

**Un invitado por navegador.** El alta cierra y borra el que traiga la cookie,
si lo hay. Sin eso, pulsar dos veces «entrar como invitado» dejaba tantos
invitados como pulsaciones y **ninguno se purgaba**: el barrido se lleva a los
que no tienen sesión viva, y esos la tenían — solo que ya no la usaba nadie.
Medido: tres pulsaciones, tres filas, cero purgadas, y el cupo consumido hasta
que caducaran. Solo actúa sobre invitados: una cuenta registrada que pulse el
botón no pierde su sesión.

**La frontera de lo que puede hacer pasa a ser una decisión de producto
escrita en un test.** Antes se derivaba del código: «endpoint con rama
`is_guest`» era exactamente el conjunto que sabía trabajar contra la
GuestSession. Esa regla murió con ella, así que
`tests/api/test_guest_boundary.py` es ahora el sitio donde la frontera está
declarada. Abierto: **todo su espacio personal**. Cerrado: admin, users,
billing, settings de plataforma, cuentas OAuth externas, PATs y el
emparejamiento de VS Code, grupos, social y publicar en Explorar.

Las dos exclusiones que cuesta más ver: **los PATs y el emparejamiento de VS
Code** quedan fuera porque son credenciales de largo recorrido que
sobrevivirían a la sesión que las emitió, y **publicar en Explorar** porque un
invitado publicando deja en la vitrina recursos que se desvanecen cuando
expira.

## La excepción: el log

«Todo lo suyo desaparece» tiene un límite deliberado: **`app_logs` no se toca**.
La purga se lleva sus recursos, su sesión y su fila, pero el rastro de lo que
hizo se queda. La demo es una ruta pública y sin registro un invitado sería un
usuario del que no consta nada — que es exactamente lo que necesita quien
reconstruye un abuso a posteriori. La retención de esas líneas la decide el
admin, como la del resto del log.

Para que ese rastro sea utilizable hacían falta dos cosas:

- **El alta se registra aparte.** `_username_for_log` lee la cookie de la
  petición, y en `POST /api/auth/guest` la cookie se emite en la respuesta: esa
  línea salía anónima. Tenía la IP pero no decía qué invitado había nacido de
  ella, que es justo el eslabón que ata una cosa con la otra. El handler emite
  ahora su propia línea con ambos.
- **El logger vuelca de verdad.** El hilo `flog-flush` moría al arrancar:
  uvicorn llama a `dictConfig`, que cierra todos los handlers ya registrados, y
  nuestro `close()` corta el hilo. El handler seguía vivo pero sin quien
  vaciara el buffer, así que solo llegaban a `app_logs` los lotes completos de
  50 y los ERROR — en una instalación tranquila, las últimas hasta 49 líneas no
  aparecían en el visor. `_ensure_flusher()` lo recupera en cada `emit`. Es un
  fallo anterior a todo esto y afectaba a **todo el log**, no solo al invitado;
  se ve aquí porque aquí el log es lo único que queda.

## Alternativas descartadas

- **Afinidad de sesión por cookie en el proxy** — la alternativa barata del 001.
  Resuelve el síntoma del worker y ninguno de los otros dos: el tope seguiría
  siendo por proceso, el invitado seguiría sin poder guardar una conversación y
  cerrar sesión seguiría sin borrar nada. Además ata la demo a una configuración
  del proxy que no está en ningún repositorio.
- **Persistir la `GuestSession` en una tabla propia** — cambia dónde vive el
  dict, no el hecho de que haya dos escrituras por endpoint. Las 107 ramas
  seguirían ahí, y con ellas la razón por la que el invitado nunca llegó a tener
  tools ni workflows.
- **Promocionar el invitado a cuenta al registrarse**, conservando su trabajo.
  Es la mejor conversión posible y el diseño la deja a un paso, pero es una
  decisión de producto aparte: hoy registrarse abre una cuenta limpia y el
  invitado se purga.

## Consecuencias

- Los invitados **escriben en la base de datos**. Eso es carga real de una
  ruta pública: la contienen el limiter `auth-guest` por IP (que cuenta en la
  BD desde el [009](009-cuota-compartida-y-por-principal.md)), el tope
  `GAIA_MAX_GUEST_SESSIONS` —que ahora es el del clúster y no el del proceso— y
  `max_request_bytes` para el tamaño de lo que suben
  ([011](011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md)).
- Con `GAIA_MAX_GUEST_SESSIONS=0` la demo queda apagada y el alta responde 503.
  Antes eso pasaba sin que nada lo dijera; ahora la auditoría de configuración
  lo declara (`guest_demo`), y `/api/admin/stats` publica `guests_active` y
  `guests_max` — los invitados no suman a `users_total`, pero son quienes
  consumen el cupo, así que el admin necesita verlos en alguna parte.
- **Toda consulta que liste o cuente usuarios tiene que excluirlos**, o el
  invitado aparece en el buscador de personas, en el panel de administración y
  en las estadísticas, apareciendo y desapareciendo entre dos recargas. Está
  hecho en `queries/users.sql`, `auth:list_users`, `billing:list_users`,
  `admin_stats:user_counts`, `explore:user_id_by_username` y el perfil público
  de `routes/users.py`. **Una consulta nueva sobre `users` hereda este
  requisito.**
- `purge_user_data` gana `user_agent_preferences`, que estaba fuera de la purga
  —como el resto de tablas indexadas por username— y ahora es la preferencia de
  conexión que el invitado escribe desde el chat.
- La caché de rol de `_get_user_auth_state` sigue resolviendo al invitado sin
  consultar, pero `_resolve_principal` ya no: leer su fila es lo que hace que un
  invitado purgado con la cookie todavía en el navegador reciba un 401 en vez de
  seguir operando.

## Qué lo sostiene

- `tests/test_guest_cap.py` — alta, rol de la fila, tope, y el tope contando en
  la BD tras recargar el módulo (que es lo más cerca de «otro worker» que se
  puede llegar en un test), más las dos caras del margen de gracia.
- `tests/api/test_guest_boundary.py` — la frontera en ambas direcciones, y que
  publicar sigue cerrado en las cuatro rutas que publican.
- `tests/api/test_guest_ciclo_de_vida.py` — el logout purga; **el logout de una
  cuenta normal no**; un invitado sin fila no pasa aunque su sesión siga viva;
  no aparece en el panel, ni en el buscador, ni en las estadísticas.
- `tests/config/test_maintenance.py` — que la purga de invitados tiene quien la
  llame. Cuelga del bucle del RGPD, así que puede quedarse sin conductor sin que
  falle nada visible.
- `tests/api/test_guest_rastro_en_el_log.py` — que el alta deja IP e id juntos y
  que la purga **no** se lleva el log.
- `tests/utils/test_flog.py` — que el hilo de volcado revive si alguien cierra
  el handler, que es lo que hace uvicorn al arrancar.
