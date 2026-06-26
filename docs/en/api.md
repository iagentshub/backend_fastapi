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

### Groups

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/teams` | List all groups; each item includes `member_count` and `resource_count` |
| `GET` | `/api/admin/teams/{id}` | Group detail: info, member list, and shared content (with resolved names) |
| `DELETE` | `/api/admin/teams/{id}` | Delete a group and all its members, invitations, and shared resources |

### Stats

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/stats` | Aggregate counters: users, agents, connections, knowledge items, total tokens |

### Logs

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/admin/logs` | Admin | List of available dates (`["YYYYMMDD", …]`), descending order |
| `GET` | `/api/admin/logs/summary` | Admin | Per-file summary with BE/FE breakdown — see structure below |
| `GET` | `/api/admin/logs/{date}` | Admin | Full content of `{date}.log` as plain text |
| `POST` | `/api/admin/logs/client` | User | Receives a log entry from the frontend and writes it to today's log file |

**`GET /api/admin/logs/summary`** — response (array):
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
The `be_*` / `fe_*` fields break down the totals by origin: backend (lines without `[frontend]`) and frontend (lines with `[frontend]`).

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

Skills have three visibility states: **public** (accessible to everyone), **private** (owner only), and **shared** (private but shared with one or more groups — recipients see it with `_shared: true`).

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/skills` | List all available skills (`?scope=all\|public\|private`); skills shared with the user appear with `_shared: true` |
| `GET` | `/api/skills/{scope}/{id}` | Get a specific skill definition |
| `POST` | `/api/skills/{scope}` | Save a skill (private scope only); `owner_id` is automatically set to the authenticated user |
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

## User settings

Per-user preferences and dashboard configuration. All endpoints require authentication.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/settings` | Get the current user's preferences (`theme`, `language`) |
| `PUT` | `/api/settings` | Update one or both preferences |
| `GET` | `/api/settings/dashboard-layout` | Get the dashboard panel order (`{"layout": ["summary","token-usage",…]}`) |
| `PUT` | `/api/settings/dashboard-layout` | Save the panel order; validates that IDs correspond to known widgets |
| `GET` | `/api/settings/dashboard-config` | Get per-widget configuration (`{"config": {"token-usage": {…}, …}}`) |
| `PUT` | `/api/settings/dashboard-config` | Save per-widget configuration for the current user |

**PUT `/api/settings` body** (all fields optional):
```json
{ "theme": "dark-red", "language": "es" }
```

Valid `theme` values: `dark-red`, `dark-blue`, `dark-orange`, `dark-purple`, `light-red`, `light-blue`, `light-orange`, `light-purple`. Legacy names `noir`, `marble`, `ember`, `ocean`, `forest`, `dusk` remain valid for backward compatibility. Valid `language` values: `es`, `en`.

**Example PUT `/api/settings/dashboard-layout` body**:
```json
{ "layout": ["summary", "token-usage", "activity", "conn-status", "recent"] }
```

Known widget IDs: `summary`, `token-usage`, `activity`, `conn-status`, `recent`.

---

## Groups

Group management within workspaces. Groups are subsets of workspace members used to share resources.

### User groups

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/workspaces/{workspace_id}/groups` | List groups in the workspace |
| `POST` | `/api/workspaces/{workspace_id}/groups` | Create a group in the workspace |
| `PATCH` | `/api/workspaces/{workspace_id}/groups/{group_id}` | Rename a group |
| `DELETE` | `/api/workspaces/{workspace_id}/groups/{group_id}` | Delete a group |
| `GET` | `/api/workspaces/{workspace_id}/groups/{group_id}/members` | List group members |
| `POST` | `/api/workspaces/{workspace_id}/groups/{group_id}/members` | Add a member to the group |
| `DELETE` | `/api/workspaces/{workspace_id}/groups/{group_id}/members/{username}` | Remove a member from the group |

### Groups (admin)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/groups` | List all groups; each item includes `member_count` and `resource_count` |
| `DELETE` | `/api/admin/groups/{group_id}` | Delete a group and all its members and shared resources |

---

## Resource Sharing

Allows sharing private resources (agents, skills, connections, knowledge) with groups. Only the owner can share or unshare their resource.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sharing/{type}/{resource_id}` | List groups a resource is shared with |
| `POST` | `/api/sharing/{type}/{resource_id}` | Share a resource with a group (`body: {"group_id": "..."}`) |
| `DELETE` | `/api/sharing/{type}/{resource_id}/{group_id}` | Stop sharing a resource with a group |
| `GET` | `/api/sharing/by-group/{group_id}/{type}` | Resources of a given type shared with a group (requires membership) |

Valid values for `{type}`: `agent`, `skill`, `connection`, `knowledge`.

Resources shared with the user appear in the standard listing endpoints (`/api/skills`, `/api/agents`, etc.) with `_shared: true`. Recipients can use them but cannot edit or export them.

---

## Connections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/connections/providers` | List all available provider types (with field definitions) |
| `GET` | `/api/connections` | List configured connections (API keys excluded); each item includes `tokens_in` and `tokens_out` with cumulative token usage |
| `GET` | `/api/connections/{id}` | Get details of a single connection |
| `POST` | `/api/connections` | Add or update a connection |
| `DELETE` | `/api/connections/{id}` | Remove a connection |
| `POST` | `/api/connections/{id}/test` | Test a single connection |
| `POST` | `/api/connections/test-all` | Test all (or selected) connections; each result includes `latency_ms` (integer in milliseconds, `null` if no test provider is available) |
| `GET` | `/api/connections/tokens-daily` | Get the daily token consumption history (`?days=N`, default 14) |
