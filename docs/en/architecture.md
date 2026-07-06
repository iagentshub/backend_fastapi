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
| **Memory** | Stores and retrieves each agent's persistent context between conversations |
| **Connections** | Manages credentials and communication with AI providers |
| **Workspaces** | Multi-tenancy: groups of users who share resources |

---

## Storage

The system uses **SQLite** (development) or **PostgreSQL** (production), selected automatically based on `DATABASE_URL`. Agent and skill files are stored on disk under `AGENTS_DIR` and `SKILLS_DIR`.

The `PH` helper in `app/storage/db.py` abstracts SQL dialect differences (`?` in SQLite, `%s` in PostgreSQL). Never interpolate user values directly into SQL strings.

---

## Access control

There are two ways to access the platform:

**Email/password** — the access method for registered users. Accounts are created via the registration flow.

**Guest access** — allows using the platform without an account. Guest access has limited permissions.

Sessions are managed via **JWT in an HttpOnly cookie** (`ga_token`). Tokens include an `iat` (issued-at) claim that is validated against `password_changed_at` in the database — changing the password immediately invalidates all previous sessions.

---

## Security

- **HTTP headers**: `SecurityHeadersMiddleware` adds CSP, HSTS, X-Frame-Options and other security headers on every response.
- **Rate limiting**: sensitive endpoints (login, register, hub-sync, social operations) have per-IP limits.
- **SSRF**: `assert_safe_url()` blocks HTTP requests to private ranges and loopback addresses.
- **Cookies**: always `httponly`, `samesite=lax`, `secure` (in production), `max_age=43200`.
