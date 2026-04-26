<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/data.md">🇬🇧 Read in English</a>
</div>

<br>

# Directorio de datos

El backend espera la siguiente estructura en `GAIA_DATA_DIR` (valor por defecto: `./data`):

```
data/
  settings.json             ← credenciales de admin, fallback de jwt_secret
  users.json                ← cuentas de usuario registradas (se crea automáticamente)
  agents/
    {agent-id}/
      config.json           ← modelo, system prompt, skills adjuntas
  connections/
    connections.json        ← credenciales de proveedores (mantener privado)
  memory/
    {agent-id}.md           ← memoria en Markdown libre por agente
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

## Qué se versiona y qué se ignora

| Ruta | Versionado | Motivo |
|---|---|---|
| `data/settings.json` | Sí | Config por defecto (sin secretos reales) |
| `data/users.json` | No | Datos de cuentas de usuario |
| `data/agents/` | No | Específico del usuario, puede contener prompts privados |
| `data/connections/connections.json` | No | Contiene claves API |
| `data/memory/` | No | Datos personales del usuario |
| `data/skills/public/` | No | Gestionado externamente (clonado por iAgentsHub) |
| `data/skills/private/` | No | Específico del usuario |

---

## Formato de una skill

Cada skill es un fichero Markdown con front matter YAML:

```markdown
---
id: mi-skill
name: Mi Skill
description: Qué hace esta skill.
icon: 🔧
category: productividad
---

El contenido de la skill va aquí. Se inyecta en el system prompt del agente.
```
