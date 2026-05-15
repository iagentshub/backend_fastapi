<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/data.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Data

The backend stores most information in an SQLite database and an external data directory mounted into the service.

---

## Database

The SQLite database (`hub.db`) stores all structured data:

| Table | Contents |
|---|---|
| `users` | User accounts — credentials, role, and per-user preferences (theme, language) |
| `accounts` | Provider API keys linked per user (Anthropic, OpenAI, GitHub, Ollama, NVIDIA, Google) — keys encrypted at rest |
| `connections` | Named AI connections with model selection and cumulative token usage — API keys encrypted at rest |
| `knowledge_items` | Knowledge base entries |
| `conversations` | Conversation history (id, title, timestamps) |
| `messages` | Individual messages linked to conversations |

---

## File directory

| Path | Contents |
|---|---|
| `agents/` | Agent configurations (instructions, model, assigned skills) |
| `memory/` | Memory accumulated by each agent between conversations |
| `skills/public/` | Skills synced from the skills repository |
| `skills/private/` | Installation-specific private skills |

---

## What is committed

None of the runtime data is committed to the repository. The database and data directory contain installation-specific information.

---

## Skills

Skills are text files with a metadata header (name, description, icon, category) followed by the skill content. The content is injected into the agent's system prompt when the skill is enabled.
