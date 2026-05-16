<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/api.md">🇪🇸 Ver en Español</a>
</div>

<br>

# API Reference

All endpoints require authentication via HTTP-only cookie (`ga_token`) unless marked **—**.

---

## Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Obtain a session cookie (rate-limited: 5 failures / 5 min) |
| `POST` | `/api/auth/register` | — | Create a new account (rate-limited: 5 / hour per IP) |
| `POST` | `/api/auth/logout` | Required | Invalidate the session cookie |
| `GET` | `/api/auth/me` | Required | Get the authenticated user's profile (includes `role`) |
| `POST` | `/api/auth/change-password` | Required | Change the current user's password |

Authentication uses **HTTP-only cookies** (`ga_token`). The `/api/auth/me` response includes a `role` field (`"admin"` or `"standard"`).

---

## Admin

All admin endpoints require the `admin` role.

### Users

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/users` | List all registered users (password hashes excluded) |
| `PATCH` | `/api/admin/users/{username}` | Update user fields (`role`, `is_active`) |
| `DELETE` | `/api/admin/users/{username}` | Delete a user (cannot self-delete) |

### Agents

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/agents` | List all private agents; each item includes `owner_id` and `owner_email` |
| `DELETE` | `/api/admin/agents/{id}?scope=private` | Delete a private agent |

### Connections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/connections` | List all connections; each item includes `owner_email` and token totals |
| `DELETE` | `/api/admin/connections/{id}` | Delete a connection (removes associated token history) |

### Knowledge

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/knowledge` | List all knowledge items; each item includes `owner_email` and `char_count` |
| `DELETE` | `/api/admin/knowledge/{id}` | Delete a knowledge item |

### Stats

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/stats` | Aggregate counters: users, agents, connections, knowledge items, total tokens |

### Logs

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/admin/logs` | Admin | List of available dates (`["YYYYMMDD", …]`), descending order |
| `GET` | `/api/admin/logs/summary` | Admin | Per-file summary: date, size, line count, errors, and warnings |
| `GET` | `/api/admin/logs/{date}` | Admin | Full content of `{date}.log` as plain text |
| `POST` | `/api/admin/logs/client` | User | Receives a log entry from the frontend and writes it to today's log file |

**`POST /api/admin/logs/client`** — body:
```json
{ "level": "INFO", "message": "message text" }
```
Valid levels: `DEBUG`, `INFO`, `OK`, `WARNING`, `ERROR`. The entry is written with a `[frontend]` tag in the current day's log file.

---

## Agents

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/agents` | List all agents |
| `POST` | `/api/agents` | Create a new agent |
| `GET` | `/api/agents/{id}` | Get agent details |
| `PUT` | `/api/agents/{id}` | Update agent configuration |
| `DELETE` | `/api/agents/{id}` | Delete an agent |
| `POST` | `/api/agents/{id}/chat` | Send a message — returns **SSE stream** |

### Chat SSE stream

The chat endpoint returns a `text/event-stream` response. Each event is a JSON object:

```
data: {"type": "chunk", "content": "Hello"}
data: {"type": "done", "reply": "Hello!", "tokens": {"in": 120, "out": 45}}
data: {"type": "error", "message": "..."}
```

The `done` event always includes a `tokens` field with the breakdown of tokens consumed in that conversation: `in` (input tokens) and `out` (output tokens). These values are automatically accumulated into the counter for the corresponding connection.

---

## Skills

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/skills` | List all available skills (`?scope=all\|public\|private`) |
| `GET` | `/api/skills/{scope}/{id}` | Get a specific skill definition |
| `POST` | `/api/skills/{scope}` | Save a skill (private scope only) |
| `DELETE` | `/api/skills/{scope}/{id}` | Delete a skill (private scope only) |

---

## Memory

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/memory` | List all memory files |
| `GET` | `/api/memory/{filename}` | Read a memory file |
| `POST` | `/api/memory/{filename}` | Write a memory file |
| `DELETE` | `/api/memory/{filename}` | Delete a memory file |

---

## Settings

Per-user preferences (theme and language). Both endpoints require authentication.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/settings` | Get the current user's preferences (`theme`, `language`) |
| `PUT` | `/api/settings` | Update one or both preferences |

**PUT body** (all fields optional):
```json
{ "theme": "noir", "language": "es" }
```

Valid values: `theme` — `noir`, `marble`, `ember`, `ocean`, `forest`, `dusk`; `language` — `es`, `en`.

Preferences are stored per user in the database. Changing them on one device is reflected on all others at next login.

---

## Connections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/connections/providers` | List all available provider types (with field definitions) |
| `GET` | `/api/connections` | List configured connections (API keys excluded); each item includes `tokens_in` and `tokens_out` fields with the cumulative token usage |
| `GET` | `/api/connections/{id}` | Get details of a single connection |
| `POST` | `/api/connections` | Add or update a connection |
| `DELETE` | `/api/connections/{id}` | Remove a connection |
| `POST` | `/api/connections/{id}/test` | Test a single connection |
| `POST` | `/api/connections/test-all` | Test all (or selected) connections |
