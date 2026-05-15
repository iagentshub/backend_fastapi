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

### Estadísticas

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/admin/stats` | Contadores agregados: usuarios, agentes, conexiones, conocimiento y tokens totales |

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

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/skills` | Listar todas las skills disponibles (`?scope=all\|public\|private`) |
| `GET` | `/api/skills/{scope}/{id}` | Obtener la definición de una skill concreta |
| `POST` | `/api/skills/{scope}` | Guardar una skill (solo scope private) |
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

## Conexiones

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/connections/providers` | Listar todos los tipos de proveedor disponibles (con definición de campos) |
| `GET` | `/api/connections` | Listar las conexiones configuradas (sin claves API); cada elemento incluye los campos `tokens_in` y `tokens_out` con el consumo acumulado |
| `GET` | `/api/connections/{id}` | Obtener los detalles de una conexión concreta |
| `POST` | `/api/connections` | Añadir o actualizar una conexión |
| `DELETE` | `/api/connections/{id}` | Eliminar una conexión |
| `POST` | `/api/connections/{id}/test` | Testar una conexión concreta |
| `POST` | `/api/connections/test-all` | Testar todas (o las indicadas) las conexiones |
