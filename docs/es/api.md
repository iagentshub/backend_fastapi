<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/api.md">🇬🇧 Read in English</a>
</div>

<br>

# Referencia de la API

Todos los endpoints requieren autenticación mediante cookie HTTP-only (`ga_token`) salvo los marcados con **—**.

## Paginación

Los catálogos de agentes, skills, prompts, tools y Knowledge aceptan `limit`
(1–100, 50 por defecto) y `offset`. El cuerpo sigue siendo una lista por
compatibilidad; `X-Total-Count` contiene el total filtrado y `X-Has-More`
indica si existe otra página. Propiedad, compartición, permisos, estado y tipo
se filtran en SQL antes de `LIMIT`.

Conversaciones y mensajes usan `limit` y un `cursor` opaco. La respuesta
publica `X-Next-Cursor` y `X-Has-More`; el cliente no debe interpretar ni
fabricar el cursor. Las páginas de mensajes mantienen orden cronológico aunque
se carguen hacia atrás.

Connections compone conexiones físicas, modelos Ollama y orquestaciones
virtuales, y Admin Explore unifica tipos heterogéneos; ambos se paginan tras la
composición, siempre con límite obligatorio. Las sesiones guest también son
colecciones acotadas en memoria. Estas excepciones están aisladas en
`pagination/materialized.py`.

---

## Autenticación

| Método | Endpoint | Auth | Descripción |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Obtener cookie de sesión (rate-limit: 5 fallos / 5 min) |
| `POST` | `/api/auth/register` | — | Crear una nueva cuenta (rate-limit: 5 / hora por IP) |
| `POST` | `/api/auth/logout` | Requerida | Invalidar la cookie de sesión |
| `GET` | `/api/auth/me` | Requerida | Obtener el perfil del usuario autenticado (incluye `role`) |
| `POST` | `/api/auth/change-password` | Requerida | Cambiar la contraseña del usuario actual |

El registro recibe `username`, `email` y `password`. El login recibe `identifier` (username o email) y `password`. El `username` es público e inmutable; el email es privado salvo que el usuario active `is_email_public`. La autenticación usa **cookies HTTP-only** (`ga_token`).

---

## Contacto público

| Método | Endpoint | Auth | Descripción |
|---|---|---|---|
| `POST` | `/api/public/contact` | — | Petición del formulario de precios (rate-limit: 5 / hora por IP) |
| `GET` | `/api/admin/contact-requests` | Admin | Últimas peticiones recibidas (`limit`, 1-500) |

Es el único endpoint que escribe sin credencial de ningún tipo. Acepta `type`
(uno de `free`, `plan_dev`, `plan_biz`, `plan_ent`, `training`), `name`, `email`
y `message`, más un campo trampa `website` que descarta la petición en silencio
si viene relleno. La petición se guarda siempre y además se avisa por correo al
buzón de la instalación (`GAIA_SMTP_FROM`): sin SMTP configurado la respuesta
trae `notified: false` y el lead queda solo en la tabla.

---

## Explorar

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/explore` | Catálogo de recursos públicos de otros usuarios |
| `GET` | `/api/explore/{resource_type}/{resource_id}/preview` | Vista previa de un recurso público |

`GET /api/explore` admite `type`, `category`, `q`, `tag`, `label` repetido,
`language` repetido, `relation`, `limit` y `offset`. Los idiomas de contenido
soportados son `es`, `en`, `fr`, `de`, `pt`, `it`, `zh`, `ja` y `ar`. Los
idiomas seleccionados se combinan entre sí con OR, pero el grupo de idioma se
combina con categoría y labels mediante AND. Por ejemplo,
`?language=es&label=production` devuelve recursos en español que además estén en
producción. Cada resultado incluye `labels`, `languages` y `linked_by_me`.

`relation` filtra por lo que el usuario ya tiene enlazado:

| Valor | Devuelve |
|---|---|
| `all` (por defecto) | El catálogo entero |
| `new` | Solo lo que el usuario **no** ha enlazado todavía |
| `linked` | Solo aquello de lo que ya tiene una copia enlazada |

Cualquier otro valor devuelve `422` con `code: invalid_field`. El valor por
defecto es `all` para no cambiar lo que ven los clientes que no envían el
parámetro; la app pide `new`, porque descubrir es ver lo que no se tiene.

Cuando `relation=new` deja la primera página vacía, la respuesta añade la
cabecera `X-Linked-Count` con cuántos resultados quedaron fuera por estar ya
enlazados. Permite distinguir «no hay nada» de «ya lo tienes todo» sin una
segunda petición, y solo se calcula en ese caso.

`GET /api/explore/official-packs` acepta el mismo `relation`. Un pack cuenta
como enlazado solo cuando lo están **todos** sus componentes (`link_state:
complete`); uno a medias (`partial`) aparece en los dos modos, porque todavía
tiene recursos que el usuario no tiene.

---

## Admin

Todos los endpoints de admin requieren el rol `admin`.

### Explorar recursos

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/explore` | Inventario unificado de usuarios, grupos, agentes, conexiones, conocimiento y orquestaciones |
| `GET` | `/api/admin/resources/{type}/{id}/graph` | Grafo relacional del objeto, cargado bajo demanda |

`/api/admin/explore` admite `type` repetido, `q`, `owner`, `limit` y `offset`. Cada elemento incluye el discriminador `resource_type`; la respuesta también devuelve `total` y contadores por tipo. Los tipos válidos son `user`, `group`, `agent`, `connection`, `knowledge` y `workflow`.

El grafo devuelve `root_id`, `nodes` y `edges`. Incluye relaciones de propiedad, pertenencia a grupos, compartición, uso de conexiones/conocimiento y participación en orquestaciones.

### Usuarios

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/users` | Listar todos los usuarios registrados (sin hashes de contraseña) |
| `PATCH` | `/api/admin/users/{username}` | Actualizar campos del usuario (`role`, `is_active`) |
| `DELETE` | `/api/admin/users/{username}` | Eliminar un usuario (no se puede eliminar a uno mismo) |

### Agentes

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/agents` | Listar todos los agentes privados; cada item incluye `owner_id` interno y `owner_username` público |
| `DELETE` | `/api/admin/agents/{id}?scope=private` | Eliminar un agente privado |

### Conexiones

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/connections` | Listar todas las conexiones; cada item incluye `owner_username` y totales de tokens |
| `DELETE` | `/api/admin/connections/{id}` | Eliminar una conexión (elimina también el historial de tokens asociado) |

### Conocimiento

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/knowledge` | Listar todos los elementos de conocimiento; cada item incluye `owner_username` y `char_count` |
| `DELETE` | `/api/admin/knowledge/{id}` | Eliminar un elemento de conocimiento |

### Fuentes oficiales

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/official-sources` | Fuentes registradas y los objetos que cada una tiene en el hub |
| `POST` | `/api/admin/official-sources/import` | Dar de alta un repositorio de GitHub y descargar su contenido |
| `POST` | `/api/admin/official-sources/{id}/sync` | Sin cuerpo, devuelve lo que trae la fuente; con `component_ids`, aplica esa selección |
| `PUT` | `/api/admin/official-sources/{id}` | Editar la fuente |
| `DELETE` | `/api/admin/official-sources/{id}` | Eliminar la fuente y todos los objetos que trajo |
| `POST` | `/api/admin/resources/{type}/{id}/official` | Marcar o desmarcar un recurso como oficial a mano |

Lo que una fuente trae **no vive en tablas propias**: se materializa como recurso normal (agente, skill, prompt, herramienta, knowledge, orquestación) propiedad del admin que sincroniza, con la label `official`, su fila pública en `resource_social` y las columnas `official_source_id` / `official_component_id` para saber de dónde salió. Por eso aparece en Explorar como una fila más, se enlaza y se forkea por las rutas de siempre y se exporta como cualquier agente.

`sync` con `component_ids` deja la fuente exactamente en esa selección: lo marcado se crea o se actualiza —con el cierre transitivo de dependencias— y lo que deja de estarlo se borra. Sin `component_ids` no cambia nada; solo devuelve `components` y `selected` para que el panel preseleccione lo que ya está.

Marcar a mano usa la fuente interna `official_by_iagentshub`, que no tiene repositorio detrás.

### Estadísticas

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/stats` | Contadores agregados: usuarios, agentes, conexiones, conocimiento y tokens totales |

### Logs

| Método | Endpoint | Auth | Descripción |
|---|---|---|---|
| `GET` | `/api/admin/logs` | Admin | Lista de fechas disponibles (`["YYYYMMDD", …]`), orden descendente |
| `GET` | `/api/admin/logs/summary` | Admin | Resumen por fichero con desglose BE/FE — ver estructura más abajo |
| `GET` | `/api/admin/logs/{date}` | Admin | Contenido completo del fichero `{date}.log` como texto plano |
| `POST` | `/api/admin/logs/client` | Usuario | Recibe una entrada de log desde el frontend y la escribe en el fichero del día |

**`GET /api/admin/logs/summary`** — respuesta (array):
```json
[
  {
    "date": "20260516",
    "size_bytes": 4096,
    "lines": 120,
    "warnings": 3,
    "errors": 1,
    "be_warnings": 2,
    "be_errors": 1,
    "fe_warnings": 1,
    "fe_errors": 0
  }
]
```
Los campos `be_*` / `fe_*` desglosan los totales por origen: backend (líneas sin `[frontend]`) y frontend (líneas con `[frontend]`).

**`POST /api/admin/logs/client`** — cuerpo:
```json
{ "level": "INFO", "message": "texto del mensaje" }
```
Niveles válidos: `DEBUG`, `INFO`, `OK`, `WARNING`, `ERROR`. La entrada se escribe con la etiqueta `[frontend]` en el fichero de log del día.

---

## Agentes

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/agents` | Listar todos los agentes |
| `POST` | `/api/agents` | Crear un nuevo agente |
| `GET` | `/api/agents/{id}` | Obtener detalles de un agente |
| `PUT` | `/api/agents/{id}` | Actualizar la configuración de un agente |
| `DELETE` | `/api/agents/{id}` | Eliminar un agente |
| `POST` | `/api/agents/{id}/chat` | Enviar un mensaje — devuelve **stream SSE** |

### Stream SSE del chat

El endpoint de chat devuelve una respuesta `text/event-stream`. Cada evento es un objeto JSON:

```
data: {"type": "chunk", "content": "Hola"}
data: {"type": "done", "reply": "¡Hola!", "tokens": {"in": 120, "out": 45}}
data: {"type": "error", "message": "..."}
```

El evento `done` incluye siempre un campo `tokens` con el desglose de tokens consumidos en esa conversación: `in` (tokens de entrada) y `out` (tokens de salida). Estos valores se acumulan automáticamente en el contador de la conexión correspondiente.

---

## Skills

Las skills tienen tres estados de visibilidad: **pública** (accesible a todos), **privada** (solo el propietario) y **compartida** (privada pero compartida con uno o más grupos — el receptor la ve con el badge `_shared: true`).

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/skills` | Listar todas las skills disponibles (`?scope=all\|public\|private`); las skills compartidas con el usuario aparecen con `_shared: true` |
| `GET` | `/api/skills/{scope}/{id}` | Obtener la definición de una skill concreta |
| `POST` | `/api/skills/{scope}` | Guardar una skill propia con scope `private` o `public`; el campo `owner_id` se fija automáticamente al usuario autenticado |
| `DELETE` | `/api/skills/{scope}/{id}` | Eliminar una skill propia; las skills públicas del sistema son de solo lectura |

La categoría debe pertenecer al catálogo cerrado (`ai`, `messaging`, `notes`, `productivity`, `dev`, `security`, `media`, `data`, `company`). El editor no admite tags libres y las `labels` recibidas por API deben pertenecer al catálogo del sistema. Los invitados pueden consultar todas las skills públicas y crear skills privadas efímeras, aisladas en memoria durante su sesión; no pueden publicar ni persistirlas en la base de datos.

---

## Memoria

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/memory` | Listar todos los ficheros de memoria |
| `GET` | `/api/memory/{filename}` | Leer un fichero de memoria |
| `POST` | `/api/memory/{filename}` | Escribir un fichero de memoria |
| `DELETE` | `/api/memory/{filename}` | Eliminar un fichero de memoria |

---

## Configuración del usuario

Preferencias y configuración del dashboard por usuario. Todos los endpoints requieren autenticación.

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/settings` | Obtener preferencias efectivas (`theme`, `language`, `theme_configurable`, `default_theme`) |
| `PUT` | `/api/settings` | Actualizar una o ambas preferencias |
| `GET` | `/api/settings/dashboard-layout` | Obtener el orden de paneles del dashboard (`{"layout": ["summary","token-usage",…]}`) |
| `PUT` | `/api/settings/dashboard-layout` | Guardar el orden de paneles; valida que los IDs correspondan a widgets conocidos |
| `GET` | `/api/settings/dashboard-config` | Obtener la configuración por widget (`{"config": {"token-usage": {…}, …}}`) |
| `PUT` | `/api/settings/dashboard-config` | Guardar la configuración por widget para el usuario actual |

**Cuerpo del PUT `/api/settings`** (todos los campos son opcionales):
```json
{ "theme": "dark-red", "language": "es", "theme_configurable": true, "default_theme": "dark-red" }
```

Valores válidos de `theme`: `dark-red`, `dark-blue`, `dark-orange`, `dark-purple`, `light-red`, `light-blue`, `light-orange`, `light-purple`. Los nombres legacy `noir`, `marble`, `ember`, `ocean`, `forest`, `dusk` siguen siendo válidos por compatibilidad. Valores válidos de `language`: `es`, `en`. Si `theme_configurable` es `false`, el backend rechaza cambios de tema y devuelve siempre el `default_theme` definido por administración; la preferencia anterior del usuario se conserva para una posible reactivación.

La configuración global se administra mediante `GET/PUT /api/settings/platform` con `users_can_configure_theme` y `default_theme`. Ambos campos también se exponen en `/api/settings/platform/public` para aplicar el tema administrado antes del login y a sesiones invitadas.

**Ejemplo de cuerpo del PUT `/api/settings/dashboard-layout`**:
```json
{ "layout": ["summary", "token-usage", "activity", "conn-status", "recent"] }
```

**Ejemplo de cuerpo del PUT `/api/settings/dashboard-config`**:
```json
{ "config": { "token-usage": { "vizType": "bars", "groupBy": "connection", "scope": "all", "limit": 5 } } }
```

Las preferencias se guardan por usuario en la base de datos. Cambiarlas en un dispositivo se refleja en todos los demás al iniciar sesión.

---

## Compartición de recursos

Permite compartir recursos privados (agentes, skills, conexiones, conocimiento) con un grupo. No mueve ni copia el recurso — solo concede acceso de uso a todos los miembros del grupo destino. El `owner_id` del recurso no cambia.

Solo el dueño directo del recurso (o un admin) puede compartirlo.

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/sharing/{type}/{resource_id}/groups` | Listar los grupos con los que está compartido un recurso |
| `POST` | `/api/sharing/{type}/{resource_id}` | Compartir un recurso con un grupo (`body: {"group_id": "..."}`) |
| `DELETE` | `/api/sharing/{type}/{resource_id}?group_id={group_id}` | Retirar el acceso de un grupo a un recurso |

Tipos válidos para `{type}`: `agent`, `skill`, `connection`, `knowledge`.

Los recursos compartidos con el usuario aparecen en los listados normales (`/api/skills`, `/api/agents`, etc.) con el campo `_shared: true`. El receptor puede usarlos pero no editarlos ni redistribuirlos.

Compartir un agente arrastra sus skills, prompts y knowledge privados —el `POST` los devuelve en `cascaded`— y **retirarlo los retira**: el `DELETE` responde con `uncascaded` (lo que ha dejado de estar compartido) y `kept` (lo que se conserva). Se conserva lo que el usuario compartió por su cuenta y lo que otro agente u orquestación compartido del mismo grupo sigue necesitando: retirarlo dejaría a ese otro recurso sin una dependencia. Una orquestación se comporta igual con los agentes que arrastró.

---

## Conexiones

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/connections/providers` | Listar todos los tipos de proveedor disponibles (con definición de campos) |
| `GET` | `/api/connections` | Listar las conexiones configuradas (sin claves API); cada elemento incluye los campos `tokens_in` y `tokens_out` con el consumo acumulado |
| `GET` | `/api/connections/{id}` | Obtener los detalles de una conexión concreta |
| `POST` | `/api/connections` | Añadir o actualizar una conexión |
| `DELETE` | `/api/connections/{id}` | Eliminar una conexión |
| `POST` | `/api/connections/{id}/test` | Testar una conexión concreta |
| `POST` | `/api/connections/test-all` | Testar todas (o las indicadas) las conexiones; cada resultado incluye `latency_ms` (entero en milisegundos, `null` si no hay proveedor de test) |
| `GET` | `/api/connections/tokens-daily` | Obtener el historial de consumo de tokens diario (`?days=N`, por defecto 14) |
