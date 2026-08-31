<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/architecture.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Backend Architecture

---

## Overview

The backend is a stateless service. All user data — agents, connections, skills, memory — is stored in a relational database and an external data directory mounted into the service. This makes backups, migration, and updates straightforward with no risk of data loss.

When the frontend sends a request, the backend authenticates it, runs the corresponding logic, and interacts with the AI provider or the file system as needed.

---

## Main components

| Component | What it does |
|---|---|
| **API** | Receives and validates requests |
| **Authentication** | Verifies user identity and controls access |
| **Agents** | Manages each agent's configuration and conversations |
| **Skills** | Loads and serves the capabilities that can be added to agents |
| **Prompts and Tools** | Manages reusable content, implementations, and versioned artifacts |
| **Memory** | Stores and retrieves each agent's persistent context between conversations |
| **Connections** | Manages credentials and communication with AI providers |
| **Groups** | Multi-tenancy: groups of users who share resources |

---

## Storage

The system uses **SQLite** (development) or **PostgreSQL** (production), selected automatically based on `DATABASE_URL`. The database is the source of truth for agents, skills, prompts, Tools, connections, Knowledge, relationships, and versions. `AGENTS_DIR` and `SKILLS_DIR` are retained only to import legacy installations; they are not active storage.

Storages write `?` placeholders for both engines. `AsyncConn` keeps them in
SQLite and translates them to `$1`, `$2`, and so on for PostgreSQL. User values
must never be interpolated directly into SQL.

---

## Listings and pagination

`app/pagination/` owns a single keyset implementation, the typed v2 envelope,
bounded metrics, exact-total timeout, and HMAC cursor codec.
Agents, Skills, Prompts, Tools, Knowledge, and Knowledge Packs traverse their
indexes with a signed temporal keyset. Explore, users, logs, and administrative
components use the same HTTP contract through a composite engine that supports
ascending, descending, and mixed orders. All of them fetch
`LIMIT + 1`, derive `has_more`, and do not run `COUNT(*)` unless the client
sends `include_total=true`; that value is carried in the signed cursor for the
rest of the traversal. There is no positional pagination branch or materialized
paginator. The import catalog uses the same design for its five kinds, with
independent filter-bound cursors.
Exact totals share a bounded per-worker slot. PostgreSQL uses asyncpg's native
timeout; SQLite interrupts its virtual machine and waits for the aiosqlite
worker to finish before reusing the connection, so timed-out work cannot keep
consuming the pool in the background.
`app/services/resource_visibility.py` centralizes ownership, shares, active
groups, and permissions so access control cannot drift from pagination across
agents, skills, prompts, tools, and Knowledge.

Migration 42 aligns agents, skills, prompts, and tools with their global visible
order (`updated_at DESC, id DESC`) and the Knowledge and Knowledge Pack import
catalogs with (`created_at DESC, id DESC`) in SQLite and PostgreSQL. It also
removes the four `*_owner_page` indexes that duplicated the canonical owner indexes.
Migration 43 aligns the public index with Explore's real order and adds derived
columns plus dedicated official-component indexes for unfiltered, state, type,
and state+type traversals. Filters run in SQL before `LIMIT + 1`; the complete
draft is no longer materialized in Python.
Migration 44 adds the physical orders for Feed, Connections, and
orchestrations. Feed ends in `(resource_type, resource_id, owner)`; Connections
merges persisted and virtual rows through `(updated_at, source_type, id)`.
Admin Explore pages identifiers from a normalized union before hydrating only
the visible page, while the table viewer keysets on simple or composite primary
keys and rejects tables without a stable key.
The default `snapshot_at` watermark excludes later inserts without holding a
database transaction open; it is not transactional isolation against updates
to an unseen item's ordering field.
`tests/performance/test_pagination.py` verifies both that a page only decodes
its own objects and that visible listings, including their keyset predicate, do
not fall back to a temporary sort.
`tests/performance/test_pagination_postgres.py` also runs `EXPLAIN (ANALYZE,
BUFFERS)` against PostgreSQL 16 with the real owner, active-group, and sharing
predicate. In the local 100,000-row measurement, the positional reference
scanned 90,050 rows in 156.429 ms while keyset scanned 50 in 3.585 ms. The
workflow runs manually and on pull requests that change pagination, SQL,
indexes, storage, or visibility; it also starts the real API on PostgreSQL and
checks Feed, Connections, Admin Explore, and metadata.

Flutter, its Dashboard, Hub Sync, and the VS Code extension consume `/api/v2`
and its strict `items + page` envelope; they do not accept legacy lists as v2
responses. The former list GETs and their transition headers have been removed;
Chat keeps its headers because it already uses a distinct cursor contract.
`/api/admin/stats` exposes bounded per-resource request, latency, page-depth,
total-reuse, and timeout metrics. These in-memory
accumulators are process-local; multi-worker deployments must aggregate them in
their observability system when they need a global view.

The public catalog (`resource_social`) follows the same rule: its order ends in
the primary key so two pages never repeat or drop a row — rows published by an
official sync share `updated_at` — and migration 24 adds the index covering that
order. Pack provenance for the page is resolved by
`KnowledgeStorage.pack_locations()`, a single query without the `content`
column, instead of one `get()` per row.


The repository has **two** keyset engines, and the difference is in the plan,
not the result: `cursor_page_query` compares tuples — `(position, id) < (?, ?)`,
a single index descent — and only works when every column shares one direction;
`composite_cursor_page` expands into `OR`/`AND` because its consumers mix
directions. They return the same rows, so swapping one for the other breaks no
test and does degrade the query.

### The admin panel too

The `/api/admin` listings were left out of that migration, and they were the
only ones in the product whose size is decided by the whole installation
rather than by a user: eleven `GET`s returned `SELECT … FROM table` with no
`WHERE` and no cap. **They were removed.** The panel's inventory is requested
through `/api/v2/admin/explore`, which already paginates and covers all eleven
types with normalised columns; of the per-type listings only
`/api/v2/admin/connections` remains, the one with an actual consumer — the LLM
connection picker of the official import.

Three things learned while paginating them:

- **The panel's keyset key carries `owner_id`.** Six of those tables have a
  composite PK `(id, owner_id)`: for a user, who only sees their own rows,
  `id` is enough; the admin sees every owner at once and there
  `(updated_at, id)` stops being unique. A keyset with a repeated key skips
  rows at the page boundary and nothing fails.
- **The owner's name comes from the `JOIN`.** Each listing called
  `_username_map` — the whole `users` table — and the panel paints several
  tabs per load: nine copies in one session.
- **The user directory's filters go to SQL.** Applying them in Python over one
  page returns silently incomplete results: the user exists, is not in the page
  that got filtered, and the screen says there is none.

`GET /api/admin/memory` was the worst of them: it fetched the `content` column
— every agent's long-term memory for every user, free text with no cap — to
call `len()` on it and throw it away. The size is now `LENGTH(content)` in SQL.
Same lesson the avatar move already wrote down, in another table.

The counts shown next to groups and users are requested only for the
identifiers on the page, instead of aggregating over the full tables. The
per-group agent count was also read by walking
`AGENTS_DIR/private/*/config.json` — files left behind by the file→DB
migration — so it reported zero on any installation created afterwards; it now
comes from `agents`.

The per-group agent count and the `agents_public`/`agents_private` figures of
`GET /api/admin/stats` were read by walking `AGENTS_DIR/*/config.json` — files
left behind by the file→DB migration — so they reported zero on any
installation created afterwards. Both now come from the `agents` table.
Migration 45 also promotes `connection_id` from the JSON blob to an indexed
column, so "which agents use this connection?" is a `COUNT(*)` instead of
fetching every agent in the installation and filtering in Python.

Removing the eleven exposed that the inventory carried the same defects: it did
`SELECT *` over `memory_files` and `len(content)` in Python — the very
truncation this work set out to remove, alive exactly where it runs — did not
exclude guests, and served neither `avatar_url` nor the counts the panel's
cards paint. Its projection now carries `LENGTH(content)`, the avatar `JOIN`,
and counts requested only for the identifiers on the page.

`tests/api/test_listados_con_cota.py` is what keeps this from coming back: it
walks `app/api/routes/` and fails when a `GET` returns a list with no `limit`
and no `cursor`. What was already uncapped is declared in its `DEUDA` with the
reason, and that list can only shrink.

## Access control

There are two ways to access the platform:

**Username or email plus password** — the public username and private email identify the same account. Personal relationships and resources use the internal `users.id`, never the username; resources in a group space use its `group_id`.

**Guest access** — allows using the platform without an account. Guest access has limited permissions.

Sessions are managed via **JWT in an HttpOnly cookie** (`ga_token`). Tokens include an `iat` (issued-at) claim that is validated against `password_changed_at` in the database — changing the password immediately invalidates all previous sessions.

---

## Security

- **HTTP headers**: `SecurityHeadersMiddleware` adds CSP, HSTS, X-Frame-Options and other security headers on every response.
- **Rate limiting**: sensitive endpoints (login, register, hub-sync, social operations) have per-IP limits.
- **SSRF**: `assert_safe_url()` blocks HTTP requests to private ranges and loopback addresses.
- **Cookies**: always `httponly`, `samesite=lax`, `secure` (in production), `max_age=43200`.
