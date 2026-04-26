<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/data.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Data Directory

The backend expects the following structure at `GAIA_DATA_DIR` (default: `./data`):

```
data/
  settings.json             ← admin credentials, jwt_secret fallback
  users.json                ← registered non-admin accounts (auto-created)
  agents/
    {agent-id}/
      config.json           ← model, system prompt, attached skills
  connections/
    connections.json        ← provider credentials (keep private)
  memory/
    {agent-id}.md           ← free-form Markdown memory per agent
  skills/
    public/
      {lang}/
        {skill-id}/
          SKILL.md
    private/
      {skill-id}/
        SKILL.md
```

---

## What is committed vs. gitignored

| Path | Committed | Reason |
|---|---|---|
| `data/settings.json` | Yes | Default config (no real secrets) |
| `data/users.json` | No | User-specific account data |
| `data/agents/` | No | User-specific, may contain private prompts |
| `data/connections/connections.json` | No | Contains API keys |
| `data/memory/` | No | Personal user data |
| `data/skills/public/` | No | Managed externally (cloned by iAgentsHub) |
| `data/skills/private/` | No | User-specific |

---

## Skill format

Each skill is a Markdown file with YAML front matter:

```markdown
---
id: my-skill
name: My Skill
description: What this skill does.
icon: 🔧
category: productivity
---

Skill content goes here. This is injected into the agent's system prompt.
```
