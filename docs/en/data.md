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
| `users` | User accounts — credentials, role, and per-user preferences (theme, language); global policy may override the effective theme without deleting the personal preference |
| `accounts` | Provider API keys linked per user (Anthropic, OpenAI, GitHub, Ollama, NVIDIA, Google), each with its own `id` — several accounts of the same provider are allowed; keys encrypted at rest |
| `connections` | Named AI connections with model selection and cumulative token usage — API keys encrypted at rest |
| `knowledge_items` | Knowledge base entries, including content-language labels |
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

Skills are stored in the database with a name, description, icon, a category from the closed catalog, and their content. They do not accept free-form tags; their labels are limited to the system catalog. System public skills are read-only; user-created skills retain their owner whether private or public. The content is injected into the agent's system prompt when the skill is enabled.

## Content language

Textual resources may carry zero or more `lang_*` labels: `lang_es`, `lang_en`,
`lang_fr`, `lang_de`, `lang_pt`, `lang_it`, `lang_zh`, `lang_ja`, and `lang_ar`.
No language label means that a language was not declared or does not apply. For
agents, the legacy `language` field remains for compatibility and is mirrored
to the first language label; labels are canonical for catalog and search.
