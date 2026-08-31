<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/api.md">🇪🇸 Ver en Español</a>
</div>

<br>

# API Guide

All endpoints require authentication via HTTP-only cookie (`ga_token`) unless marked **—**.

This guide describes the most important usage contracts; it does not duplicate
all 320 routes in the executable contract. The canonical list is maintained in
`tests/api/contrato_rutas.txt`. In development (`GAIA_DEV_MODE=true`), OpenAPI
is also available at `/openapi.json` and the interactive UI at `/docs`.

## Pagination

All 15 v2 catalogs —agents, skills, prompts, tools, Knowledge, Knowledge Packs,
imports, Explore, users, Feed, Connections, Admin Explore, table data, logs,
and official-draft components— use keyset pagination. The next
request sends the opaque `page.next_cursor` back as
`cursor`. Their body is self-contained:

```json
{"items": [], "page": {"has_more": false, "next_cursor": null,
 "total": null, "snapshot_at": "2026-08-30T10:00:00+00:00"}}
```

The former list GETs have been removed from both OpenAPI and the router. There
is no positional compatibility route: first- and third-party clients must use
v2. Chat already used cursors and temporarily keeps its header-based contract.

An exact total is not computed by default. `include_total=true` computes it once
under a timeout and carries it in following signed cursors, so a traversal does
not repeat `COUNT(*)`. Timeout returns `503 pagination_total_timeout`;
detecting another page only needs `LIMIT + 1`. `offset` receives
`422 invalid_field`.
The budget includes waiting for the worker's exact-total slot. On expiry,
asyncpg cancels PostgreSQL work; SQLite calls `interrupt()` and drains the
operation before returning that connection to the pool.

`consistent=true` (default) records a high-water mark on the first page and
excludes later inserts. It is not a long-lived transaction: updating an unseen
item's ordering field may move it. `consistent=false` disables the watermark.
Cursors are HMAC-signed, expire, and are bound to user, resource, and filters.

The `/api/v2/agents/import/catalog/{kind}` search endpoint is also cursor-only for
all five kinds (`skill`, `prompt`, `tool`, `knowledge`, and `knowledge_pack`).
Its body uses the same `items + page` envelope; `total` is computed only with
`include_total=true`. The v1 list GET was removed; batched resolution keeps
`POST /api/agents/import/catalog/resolve` because it is not a paginated list.

Conversations and messages also use `limit` and an opaque `cursor`. Responses expose
`X-Next-Cursor` and `X-Has-More`; clients must not inspect or manufacture the
cursor. Message pages remain chronological while older pages load backwards.

Connections keyset-pages persisted connections and virtual orchestrations by
`(updated_at, source_type, id)`. A normal list never calls Ollama;
`include_models=true` nests variants under their base connection instead of
creating fake paginated rows. Admin Explore pages identifiers from its
normalized union before hydrating only the visible page. The table viewer uses
each table's simple or composite primary key as an ascending keyset.

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

## Public contact

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/public/contact` | — | Pricing contact form submission (rate-limited: 5 / hour per IP) |
| `GET` | `/api/admin/contact-requests` | Admin | Latest submissions (`limit`, 1-500) |

The only endpoint that writes without any credential. It accepts `type` (one of
`free`, `plan_dev`, `plan_biz`, `plan_ent`, `training`), `name`, `email` and
`message`, plus a `website` honeypot that silently discards the submission when
filled in. The request is always stored and also emailed to the instance mailbox
(`GAIA_SMTP_FROM`): without SMTP configured the response carries
`notified: false` and the lead only lives in the table.

---

## Explore

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v2/explore` | Cursor-based catalog of public resources owned by other users |
| `GET` | `/api/explore/{resource_type}/{resource_id}/preview` | Preview a public resource |

`GET /api/v2/explore` accepts `type`, `category`, `q`, `tag`, repeated `label`,
repeated `language`, `relation`, `limit`, and `cursor`. Its stable mixed order
is `updated_at DESC, stars_count DESC, resource_type, resource_id, owner`, with
no `OFFSET`. Supported content
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

User search uses `GET /api/v2/users?q=...&limit=...&cursor=...`, ordered by
`username, id`. Neither v2 list computes a total unless `include_total=true`
is requested.

When `relation=new` leaves the first page empty, the v2 response adds a
`linked_matches` field with how many results were left out for being already
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
| `GET` | `/api/v2/admin/explore` | Cursor-based unified inventory of users, groups, and resources |
| `GET` | `/api/admin/resources/{resource_type}/{resource_id}/relations` | Object relationships used by the client to build its graph |

`/api/v2/admin/explore` accepts repeated `type`, `q`, `owner`, `role`, `active`,
`verified`, `knowledge_type`, `limit`, and `cursor`. Every item includes
`resource_type`. `include_counts=true` adds all eleven bounded counters and
`include_total=true` requests the exact total. Valid types are `user`, `group`,
`agent`, `connection`, `knowledge`, `workflow`, `llm_orchestration`, `skill`,
`prompt`, `tool`, and `memory`.

The table viewer uses
`GET /api/v2/admin/metadata/tables/{table_name}/data?q=...&limit=...&cursor=...`.
The allowlist and sensitive-column masking remain server-side; a table without
a stable primary key returns `409 pagination_key_unavailable`.

The backend returns typed relationships; Flutter builds `nodes` and `edges`
from them. There is no `/graph` endpoint: keeping graph assembly in the client
avoids a different representation for every interface.

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
| `GET` | `/api/v2/admin/official-source-drafts/{draft_id}/components` | Cursor page with `component_type`, `state`, and `q` filters |
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
| `GET` | `/api/v2/admin/logs` | Admin | Filtered cursor viewer ordered by `ts DESC, id DESC` |
| `GET` | `/api/admin/logs/summary` | Admin | Daily summary with BE/FE breakdown |
| `GET` | `/api/admin/logs/export` | Admin | Full CSV export for the current filters |
| `POST` | `/api/admin/logs/client` | Admin | Stores a frontend log entry |

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
| `GET` | `/api/v2/agents` | List agents by cursor |
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
| `GET` | `/api/v2/skills` | List skills by cursor (`?scope=all\|public\|private`); shared skills include `_shared: true` |
| `GET` | `/api/skills/{scope}/{id}` | Get a specific skill definition |
| `POST` | `/api/skills/{scope}` | Save an owned skill with `private` or `public` scope; `owner_id` is automatically set to the authenticated user |
| `DELETE` | `/api/skills/{scope}/{id}` | Delete an owned skill; system public skills are read-only |

The category must belong to the closed catalog (`ai`, `messaging`, `notes`, `productivity`, `dev`, `security`, `media`, `data`, `company`). The editor does not accept free-form tags, and API `labels` must belong to the system catalog. Guests can browse every public skill and create ephemeral private skills isolated in session memory; they cannot publish or persist them in the database.

---

## Tools

A Tool combines instructions for the agent with an optional implementation.
Current API values are `python`, `shell`, and `cpp`; the effective catalog,
extensions, and native targets are exposed by
`GET /api/settings/platform/public` under `tool_runtimes`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v2/tools` | Cursor-paginated list of accessible Tools |
| `GET` | `/api/tools/{scope}/{tool_id}` | Metadata and textual implementation, never the binary BLOB |
| `POST` | `/api/tools/{scope}` | Create or update metadata and record a version |
| `DELETE` | `/api/tools/{scope}/{tool_id}` | Delete an owned Tool |
| `POST` | `/api/tools/{scope}/{tool_id}/activate` | Activate a Tool |
| `POST` | `/api/tools/{scope}/{tool_id}/deactivate` | Deactivate a Tool without removing its links |
| `POST` | `/api/tools/{scope}/{tool_id}/binary` | Upload a `cpp` Tool artifact as multipart |
| `GET` | `/api/tools/{scope}/{tool_id}/binary` | Download the artifact with `Content-Length` and SHA-256 `ETag` |
| `POST` | `/api/tools/{scope}/{source_id}/link` | Link an approved public Tool |
| `POST` | `/api/tools/private/{tool_id}/sync` | Synchronize a linked copy |
| `PUT` | `/api/tools/{scope}/{tool_id}/visibility` | Publish to or remove from the social catalog |

Python and Shell store their script in `content`. `cpp` is a historical wire
value: the server stores a compiled native executable with `target_os`,
`target_arch`, filename, size, uploader, and SHA-256. C++ source may be retained,
but it does not replace the artifact. The request limit is the single
Admin-controlled `max_request_bytes`; `0` means unlimited.

Every new or modified implementation receives the `review` label. Admin may
move it to `approved`, back to `review`, or to `quarantine` using the existing
labels. A Tool under review cannot be published, shared, inherited, or consumed
by third parties; a quarantined Tool cannot be consumed by anyone. A disabled
Tool keeps its links but is not injected or consumed.

Tool versions use the common
`/api/resources/tool/{resource_id}/versions` routes. Artifacts are retained by
content, and a restore changes metadata, binary link, and history in one
transaction.

The backend **does not execute Tools**. It serves instructions, scripts, and
artifacts for authorized clients. Flutter checks compatibility and SHA-256, but
automatic local execution is not enabled yet.

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

Allows sharing private resources with a group. The resource is not moved or copied — access is granted to all members of the destination group. The `owner_id` of the resource does not change.

Only the direct owner of the resource (or an admin) can share it.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sharing/{type}/{resource_id}/groups` | List groups a resource is shared with |
| `POST` | `/api/sharing/{type}/{resource_id}` | Share a resource with a group (`body: {"group_id": "..."}`) |
| `DELETE` | `/api/sharing/{type}/{resource_id}?group_id={group_id}` | Revoke a group's access to a resource |

Valid values for `{type}`: `agent`, `skill`, `connection`, `knowledge`,
`knowledge_pack`, `workflow`, `llm_orchestration`, `prompt`, and `tool`.

Resources shared with the user appear in the v2 listings (`/api/v2/skills`, `/api/v2/agents`, etc.) with `_shared: true`. Recipients can use them but cannot edit or redistribute them.

Sharing an agent cascades to its private skills, prompts, Tools, and Knowledge — the `POST` returns them in `cascaded` — and **revoking it revokes them too**: the `DELETE` responds with `uncascaded` (no longer shared) and `kept`. Anything the user shared on its own is kept, as is anything another shared agent or orchestration in the same group still needs: removing it would leave that resource without a dependency. Orchestrations behave the same way with the agents they brought in.

---

## Connections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/connections/providers` | List all available provider types (with field definitions) |
| `GET` | `/api/v2/connections` | Cursor-list accessible connections and orchestrations, always without secrets |
| `GET` | `/api/connections/{id}` | Get details of a single connection |
| `POST` | `/api/connections` | Add or update a connection |
| `DELETE` | `/api/connections/{id}` | Remove a connection |
| `POST` | `/api/connections/{id}/test` | Test a single connection |
| `POST` | `/api/connections/test-all` | Test all (or selected) connections; each result includes `latency_ms` (integer in milliseconds, `null` if no test provider is available) |
| `GET` | `/api/connections/tokens-daily` | Get the daily token consumption history (`?days=N`, default 14) |

The list accepts `group_id`, `include_inactive`, `include_models`, `limit`, and
`cursor`. Without `include_models` it does not call providers. When enabled,
each base connection carries nested `model_variants`; selectors may flatten
them locally without changing page limits or cursors.
