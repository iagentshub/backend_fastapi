<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/architecture.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Backend Architecture

---

## Overview

The backend is a stateless service. All user data — agents, connections, skills, memory — is stored in an external data directory mounted into the service. This makes backups, migration, and updates straightforward with no risk of data loss.

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

---

## Storage

No database is used. All information is stored as files in the data directory. This keeps the system predictable, easy to back up, and portable across environments.

---

## Access control

There are two ways to access the platform:

**Google Sign-In** — the access method for registered users. No need to manage passwords in the system.

**Guest access** — allows using the platform without an account. Guest access has limited permissions.

Sessions are maintained securely without exposing sensitive information to the browser.
