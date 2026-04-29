<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/data.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Data

The backend stores all information in an external data directory mounted into the service. No database is used.

---

## What it contains

| Path | Contents |
|---|---|
| `settings.json` | System configuration (fallback JWT secret) |
| `users.json` | Registered user accounts |
| `agents/` | Agent configurations (instructions, model, assigned skills) |
| `connections/` | AI provider credentials |
| `memory/` | Memory accumulated by each agent between conversations |
| `skills/public/` | Skills synced from the skills repository |
| `skills/private/` | Installation-specific private skills |

---

## What is committed

Only `settings.json` is included in the repository as a default value. All other data is not committed: it contains installation-specific information.

---

## Skills

Skills are text files with a metadata header (name, description, icon, category) followed by the skill content. The content is injected into the agent's system prompt when the skill is enabled.
