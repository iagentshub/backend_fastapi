<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/data.md">🇬🇧 Read in English</a>
</div>

<br>

# Datos

El backend guarda la mayor parte de la información en una base de datos SQLite y en un directorio de datos externo montado en el servicio.

---

## Base de datos

La base de datos SQLite (`hub.db`) almacena todos los datos estructurados:

| Tabla | Contenido |
|---|---|
| `users` | Cuentas de usuario — credenciales, rol y preferencias por usuario (tema, idioma) |
| `accounts` | API keys de proveedor vinculadas por usuario (Anthropic, OpenAI, GitHub, Ollama, NVIDIA, Google) — keys cifradas en reposo |
| `connections` | Conexiones de IA con nombre, selección de modelo y consumo acumulado de tokens — API keys cifradas en reposo |
| `knowledge_items` | Elementos de la base de conocimiento |
| `conversations` | Historial de conversaciones (id, título, fechas) |
| `messages` | Mensajes individuales ligados a conversaciones |

---

## Directorio de ficheros

| Ruta | Contenido |
|---|---|
| `agents/` | Configuraciones de los agentes (instrucciones, modelo, skills asignadas) |
| `memory/` | Memoria acumulada por cada agente entre conversaciones |
| `skills/public/` | Skills sincronizadas desde el repositorio de skills |
| `skills/private/` | Skills privadas de la instalación |

---

## Qué se versiona

Ningún dato de runtime se incluye en el repositorio. La base de datos y el directorio de datos contienen información específica de cada instalación.

---

## Skills

Las skills son ficheros de texto con una cabecera de metadatos (nombre, descripción, icono, categoría) seguida del contenido de la skill. El contenido se inyecta en el system prompt del agente cuando la skill está activada.
