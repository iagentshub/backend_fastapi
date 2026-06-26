<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/api.md">🇬🇧 Read in English</a>
</div>

<br>

# Referencia de la API

Todos los endpoints requieren autenticación mediante cookie HTTP-only (`ga_token`) salvo los marcados con **—**.

---

## Autenticación

| Método | Endpoint | Auth | Descripción |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Obtener cookie de sesión (rate-limit: 5 fallos / 5 min) |
| `POST` | `/api/auth/register` | — | Crear una nueva cuenta (rate-limit: 5 / hora por IP) |
| `POST` | `/api/auth/logout` | Requerida | Invalidar la cookie de sesión |
| `GET` | `/api/auth/me` | Requerida | Obtener el perfil del usuario autenticado (incluye `role`) |
| `POST` | `/api/auth/change-password` | Requerida | Cambiar la contraseña del usuario actual |

La autenticación usa **cookies HTTP-only** (`ga_token`). La respuesta de `/api/auth/me` incluye un campo `role` (`"admin"` o `"standard"`).

---

## Admin

Todos los endpoints de admin requieren el rol `admin`.

### Usuarios

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/users` | Listar todos los usuarios registrados (sin hashes de contraseña) |
| `PATCH` | `/api/admin/users/{username}` | Actualizar campos del usuario (`role`, `is_active`) |
| `DELETE` | `/api/admin/users/{username}` | Eliminar un usuario (no se puede eliminar a uno mismo) |

### Agentes

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/agents` | Listar todos los agentes privados; cada item incluye `owner_id` y `owner_email` |
| `DELETE` | `/api/admin/agents/{id}?scope=private` | Eliminar un agente privado |

### Conexiones

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/connections` | Listar todas las conexiones; cada item incluye `owner_email` y totales de tokens |
| `DELETE` | `/api/admin/connections/{id}` | Eliminar una conexión (elimina también el historial de tokens asociado) |

### Conocimiento

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/knowledge` | Listar todos los elementos de conocimiento; cada item incluye `owner_email` y `char_count` |
| `DELETE` | `/api/admin/knowledge/{id}` | Eliminar un elemento de conocimiento |

### Grupos

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/teams` | Listar todos los grupos; cada item incluye `member_count` y `resource_count` |
| `GET` | `/api/admin/teams/{id}` | Detalle de un grupo: info, lista de miembros y contenido compartido (con nombres resueltos) |
| `DELETE` | `/api/admin/teams/{id}` | Eliminar un grupo y todos sus miembros, invitaciones y recursos compartidos |

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
| `POST` | `/api/skills/{scope}` | Guardar una skill (solo scope private); el campo `owner_id` se fija automáticamente al usuario autenticado |
| `DELETE` | `/api/skills/{scope}/{id}` | Eliminar una skill (solo scope private) |

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
| `GET` | `/api/settings` | Obtener las preferencias del usuario actual (`theme`, `language`) |
| `PUT` | `/api/settings` | Actualizar una o ambas preferencias |
| `GET` | `/api/settings/dashboard-layout` | Obtener el orden de paneles del dashboard (`{"layout": ["summary","token-usage",…]}`) |
| `PUT` | `/api/settings/dashboard-layout` | Guardar el orden de paneles; valida que los IDs correspondan a widgets conocidos |
| `GET` | `/api/settings/dashboard-config` | Obtener la configuración por widget (`{"config": {"token-usage": {…}, …}}`) |
| `PUT` | `/api/settings/dashboard-config` | Guardar la configuración por widget para el usuario actual |

**Cuerpo del PUT `/api/settings`** (todos los campos son opcionales):
```json
{ "theme": "dark-red", "language": "es" }
```

Valores válidos de `theme`: `dark-red`, `dark-blue`, `dark-orange`, `dark-purple`, `light-red`, `light-blue`, `light-orange`, `light-purple`. Los nombres legacy `noir`, `marble`, `ember`, `ocean`, `forest`, `dusk` siguen siendo válidos por compatibilidad. Valores válidos de `language`: `es`, `en`.

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

## Grupos

Gestión de grupos dentro de workspaces. Los grupos son subconjuntos de usuarios de un workspace y sirven para compartir recursos entre sus miembros.

### Grupos de usuario

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/workspaces/{workspace_id}/groups` | Listar los grupos del workspace |
| `POST` | `/api/workspaces/{workspace_id}/groups` | Crear un grupo en el workspace |
| `PATCH` | `/api/workspaces/{workspace_id}/groups/{group_id}` | Renombrar un grupo |
| `DELETE` | `/api/workspaces/{workspace_id}/groups/{group_id}` | Eliminar un grupo |
| `GET` | `/api/workspaces/{workspace_id}/groups/{group_id}/members` | Listar los miembros del grupo |
| `POST` | `/api/workspaces/{workspace_id}/groups/{group_id}/members` | Añadir un miembro al grupo |
| `DELETE` | `/api/workspaces/{workspace_id}/groups/{group_id}/members/{username}` | Eliminar un miembro del grupo |

### Grupos (admin)

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/groups` | Listar todos los grupos; cada item incluye `member_count` y `resource_count` |
| `DELETE` | `/api/admin/groups/{group_id}` | Eliminar un grupo y todos sus miembros y recursos compartidos |

---

## Compartición de recursos

Permite compartir recursos privados (agentes, skills, conexiones, conocimiento) con grupos. Solo el propietario puede compartir/dejar de compartir su recurso.

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/sharing/{type}/{resource_id}` | Listar los grupos con los que está compartido un recurso |
| `POST` | `/api/sharing/{type}/{resource_id}` | Compartir un recurso con un grupo (`body: {"group_id": "..."}`) |
| `DELETE` | `/api/sharing/{type}/{resource_id}/{group_id}` | Dejar de compartir un recurso con un grupo |
| `GET` | `/api/sharing/by-group/{group_id}/{type}` | Recursos de un tipo compartidos con un grupo (requiere ser miembro) |

Tipos válidos para `{type}`: `agent`, `skill`, `connection`, `knowledge`.

Los recursos compartidos con el usuario aparecen en los listados normales (`/api/skills`, `/api/agents`, etc.) con el campo `_shared: true`. El receptor puede usarlos pero no editarlos ni exportarlos.

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
