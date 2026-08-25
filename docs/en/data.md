<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/data.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Data

The backend stores structured information in SQLite or PostgreSQL. The external
directory contains the SQLite database when that engine is used, settings,
operational state, and the few resources that remain file-based.

---

## Database

The database stores all structured data. With SQLite it is `hub.db`; with
PostgreSQL it lives in the server selected by `DATABASE_URL`.

| Table | Contents |
|---|---|
| `users` | User accounts — credentials, role, and per-user preferences (theme, language); global policy may override the effective theme without deleting the personal preference |
| `accounts` | Provider API keys linked per user (Anthropic, OpenAI, GitHub, Ollama, NVIDIA, Google), each with its own `id` — several accounts of the same provider are allowed; keys encrypted at rest |
| `connections` | Named AI connections with model selection and cumulative token usage — API keys encrypted at rest |
| `knowledge_items` | Knowledge base entries, including content-language labels |
| `conversations` | Conversation history (id, title, timestamps) |
| `messages` | Individual messages linked to conversations |
| `agents`, `skills`, `prompts`, `tools` | Reusable resources, ownership, state, content, and metadata |
| `tool_artifacts`, `tool_artifact_links`, `tool_version_artifacts` | SHA-256 Tool binaries, active links, and version retention |
| `resource_versions` | Immutable history for agents, skills, and Tools |
| `groups`, `resource_group_shares` | Multi-tenancy and shared access |
| `resource_social` | Publications visible in Explore |
| `workflows`, `llm_orchestrations` | Execution definitions and LLM routes |

---

## File directory

| Path | Contents |
|---|---|
| `memory/` | Memory accumulated by each agent between conversations |
| `settings.json` | Admin-managed platform settings and local secrets |
| `centinel_state.json` | Centinel operational state |
| `agents/`, `skills/`, `connections/`, `accounts/` | Legacy migration inputs; not active sources of truth |

---

## What is committed

None of the runtime data is committed to the repository. The database and data directory contain installation-specific information.

---

## Skills

Skills are stored in the database with a name, description, icon, a category from the closed catalog, and their content. They do not accept free-form tags; their labels are limited to the system catalog. System public skills are read-only; user-created skills retain their owner whether private or public. The content is injected into the agent's system prompt when the skill is enabled.

## Tools and artifacts

Tool metadata, instructions, and scripts live in `tools`. Native executables are
never included in listing or detail JSON: they are stored once by SHA-256 in
`tool_artifacts` and linked to the Tool and its versions. A restorable backup
must therefore include the complete database; copying an assumed Tools
directory does not preserve artifacts.

## Content language

Textual resources may carry zero or more `lang_*` labels: `lang_es`, `lang_en`,
`lang_fr`, `lang_de`, `lang_pt`, `lang_it`, `lang_zh`, `lang_ja`, and `lang_ar`.
No language label means that a language was not declared or does not apply. For
agents, the legacy `language` field remains for compatibility and is mirrored
to the first language label; labels are canonical for catalog and search.
Documents uploaded as `multipart/form-data` accept the same labels through the
`labels` field encoded as JSON.
