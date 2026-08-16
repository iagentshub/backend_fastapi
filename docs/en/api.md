<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/api.md">🇪🇸 Ver en Español</a>
</div>

<br>

# API Reference

All endpoints require authentication via HTTP-only cookie (`ga_token`) unless marked **—**.

## Pagination

Agent, skill, prompt, tool, and Knowledge catalogs accept `limit` (1–100, 50
by default) and `offset`. The body remains a list for compatibility;
`X-Total-Count` reports the filtered total and `X-Has-More` reports whether a
next page exists. Ownership, sharing, permission, active-state, and type
filters run in SQL before `LIMIT`.

Conversations and messages use `limit` and an opaque `cursor`. Responses expose
`X-Next-Cursor` and `X-Has-More`; clients must not inspect or manufacture the
cursor. Message pages remain chronological while older pages load backwards.

Connections composes physical connections, Ollama models, and virtual
orchestrations, while Admin Explore unifies heterogeneous types; both are
paginated after composition with a mandatory limit. Bounded guest-session
collections are also sliced in memory. These explicit exceptions live in
`pagination/materialized.py`.

---

## Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Obtain a session cookie (rate-limited: 5 failures / 5 min) |
| `POST` | `/api/auth/register` | — | Create a new account (rate-limited: 5 / hour per IP) |
| `POST` | `/api/auth/logout` | Required | Invalidate the session cookie |
| `GET` | `/api/auth/me` | Required | Get the authenticated user's profile (includes `role`) |
| `POST` | `/api/auth/change-password` | Required | Change the current user's password |

Registration accepts `username`, `email`, and `password`. Login accepts `identifier` (username or email) and `password`. The username is public and immutable; email stays private unless the user enables `is_email_public`. Authentication uses **HTTP-only cookies** (`ga_token`).

---

## Explore

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/explore` | Catalog of public resources owned by other users |
| `GET` | `/api/explore/{resource_type}/{resource_id}/preview` | Preview a public resource |

`GET /api/explore` accepts `type`, `category`, `q`, `tag`, repeated `label`,
repeated `language`, `relation`, `limit`, and `offset`. Supported content
languages are `es`, `en`, `fr`, `de`, `pt`, `it`, `zh`, `ja`, and `ar`. Selected
languages are combined with OR, while the language group is combined with
category and labels using AND. For example, `?language=es&label=production`
returns Spanish resources that are also in production. Every result includes
`labels`, `languages`, and `linked_by_me`.

`relation` filters by what the user has already linked:

| Value | Returns |
|---|---|
| `all` (default) | The whole catalog |
| `new` | Only what the user has **not** linked yet |
| `linked` | Only what the user already has a linked copy of |

Any other value returns `422` with `code: invalid_field`. The default is `all`
so clients that omit the parameter keep seeing what they saw before; the app
asks for `new`, because discovering means seeing what you do not have.

When `relation=new` leaves the first page empty, the response adds an
`X-Linked-Count` header with how many results were left out for being already
linked. It tells "nothing found" apart from "you already have it all" without a
second request, and it is only computed in that case.

`GET /api/explore/official-packs` takes the same `relation`. A pack only counts
as linked when **all** of its components are (`link_state: complete`); a partial
one shows up in both modes, because it still holds resources the user does not
have.

---

## Admin

All admin endpoints require the `admin` role.

### Explore resources

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/explore` | Unified inventory of users, groups, agents, connections, knowledge and orchestrations |
| `GET` | `/api/admin/resources/{type}/{id}/graph` | Relationship graph for an object, loaded on demand |

`/api/admin/explore` accepts repeated `type`, `q`, `owner`, `limit`, and `offset` parameters. Every item includes the `resource_type` discriminator; the response also returns `total` and per-type counts. Valid types are `user`, `group`, `agent`, `connection`, `knowledge`, and `workflow`.

The graph response contains `root_id`, `nodes`, and `edges`, covering ownership, group membership, sharing, connection/knowledge usage, and orchestration participation.

### Users

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/users` | List all registered users (password hashes excluded) |
| `PATCH` | `/api/admin/users/{username}` | Update user fields (`role`, `is_active`) |
| `DELETE` | `/api/admin/users/{username}` | Delete a user (cannot self-delete) |

### Agents

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/agents` | List all private agents; each item includes internal `owner_id` and public `owner_username` |
| `DELETE` | `/api/admin/agents/{id}?scope=private` | Delete a private agent |

### Connections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/connections` | List all connections; each item includes `owner_username` and token totals |
| `DELETE` | `/api/admin/connections/{id}` | Delete a connection (removes associated token history) |

### Knowledge

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/knowledge` | List all knowledge items; each item includes `owner_username` and `char_count` |
| `DELETE` | `/api/admin/knowledge/{id}` | Delete a knowledge item |

### Official sources

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/official-sources` | Registered sources and the objects each one has in the hub |
| `POST` | `/api/admin/official-sources/import` | Register a GitHub repository and download its content |
| `POST` | `/api/admin/official-sources/{id}/sync` | With no body, returns what the source brings; with `component_ids`, applies that selection |
| `PUT` | `/api/admin/official-sources/{id}` | Edit the source |
| `DELETE` | `/api/admin/official-sources/{id}` | Delete the source and every object it brought |
| `POST` | `/api/admin/resources/{type}/{id}/official` | Flag or unflag a resource as official by hand |

What a source brings **does not live in tables of its own**: it is materialised as a normal resource (agent, skill, prompt, tool, knowledge, workflow) owned by the admin who syncs, with the `official` label, its public `resource_social` row, and the `official_source_id` / `official_component_id` columns recording where it came from. That is why it shows up in Explore as one more row, is linked and forked through the usual routes, and exports like any other agent.

`sync` with `component_ids` leaves the source at exactly that selection: what is selected is created or updated — with the transitive closure of its dependencies — and what is no longer selected is deleted. Without `component_ids` nothing changes; it only returns `components` and `selected` so the panel can pre-check what is already there.

Flagging by hand uses the internal `official_by_iagentshub` source, which has no repository behind it.

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
| `POST` | `/api/skills/{scope}` | Save an owned skill with `private` or `public` scope; `owner_id` is automatically set to the authenticated user |
| `DELETE` | `/api/skills/{scope}/{id}` | Delete an owned skill; system public skills are read-only |

The category must belong to the closed catalog (`ai`, `messaging`, `notes`, `productivity`, `dev`, `security`, `media`, `data`, `company`). The editor does not accept free-form tags, and API `labels` must belong to the system catalog. Guests can browse every public skill and create ephemeral private skills isolated in session memory; they cannot publish or persist them in the database.

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
| `GET` | `/api/settings` | Get effective preferences (`theme`, `language`, `theme_configurable`, `default_theme`) |
| `PUT` | `/api/settings` | Update one or both preferences |
| `GET` | `/api/settings/dashboard-layout` | Get the dashboard panel order (`{"layout": ["summary","token-usage",…]}`) |
| `PUT` | `/api/settings/dashboard-layout` | Save the panel order; validates that IDs correspond to known widgets |
| `GET` | `/api/settings/dashboard-config` | Get per-widget configuration (`{"config": {"token-usage": {…}, …}}`) |
| `PUT` | `/api/settings/dashboard-config` | Save per-widget configuration for the current user |

**PUT `/api/settings` body** (all fields optional):
```json
{ "theme": "dark-red", "language": "es", "theme_configurable": true, "default_theme": "dark-red" }
```

Valid `theme` values: `dark-red`, `dark-blue`, `dark-orange`, `dark-purple`, `light-red`, `light-blue`, `light-orange`, `light-purple`. Legacy names `noir`, `marble`, `ember`, `ocean`, `forest`, `dusk` remain valid for backward compatibility. Valid `language` values: `es`, `en`. When `theme_configurable` is `false`, the backend rejects theme changes and always returns the admin-defined `default_theme`; the previous user preference is retained in case customization is enabled again.

Global policy is managed through `GET/PUT /api/settings/platform` using `users_can_configure_theme` and `default_theme`. Both fields are also exposed by `/api/settings/platform/public` so the managed theme can be applied before login and to guest sessions.

**Example PUT `/api/settings/dashboard-layout` body**:
```json
{ "layout": ["summary", "token-usage", "activity", "conn-status", "recent"] }
```

Known widget IDs: `summary`, `token-usage`, `activity`, `conn-status`, `recent`.

---

## Resource Sharing

Allows sharing private resources (agents, skills, connections, knowledge) with a group. The resource is not moved or copied — access is granted to all members of the destination group. The `owner_id` of the resource does not change.

Only the direct owner of the resource (or an admin) can share it.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sharing/{type}/{resource_id}/groups` | List groups a resource is shared with |
| `POST` | `/api/sharing/{type}/{resource_id}` | Share a resource with a group (`body: {"group_id": "..."}`) |
| `DELETE` | `/api/sharing/{type}/{resource_id}?group_id={group_id}` | Revoke a group's access to a resource |

Valid values for `{type}`: `agent`, `skill`, `connection`, `knowledge`.

Resources shared with the user appear in the standard listing endpoints (`/api/skills`, `/api/agents`, etc.) with `_shared: true`. Recipients can use them but cannot edit or redistribute them.

Sharing an agent cascades to its private skills, prompts, and knowledge — the `POST` returns them in `cascaded` — and **revoking it revokes them too**: the `DELETE` responds with `uncascaded` (no longer shared) and `kept`. Anything the user shared on its own is kept, as is anything another shared agent or orchestration in the same group still needs: removing it would leave that resource without a dependency. Orchestrations behave the same way with the agents they brought in.

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
