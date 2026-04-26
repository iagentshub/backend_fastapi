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

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/users` | List all registered users (password hashes excluded) |
| `DELETE` | `/api/admin/users/{username}` | Delete a user (cannot self-delete) |

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
data: {"type": "done", "reply": "Hello!", "tokens": {}}
data: {"type": "error", "message": "..."}
```

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

## Connections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/connections/providers` | List all available provider types (with field definitions) |
| `GET` | `/api/connections` | List configured connections (API keys excluded) |
| `POST` | `/api/connections` | Add or update a connection |
| `DELETE` | `/api/connections/{id}` | Remove a connection |
| `POST` | `/api/connections/{id}/test` | Test a single connection |
| `POST` | `/api/connections/test-all` | Test all (or selected) connections |
