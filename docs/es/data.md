<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/data.md">🇬🇧 Read in English</a>
</div>

<br>

# Datos

El backend guarda la información estructurada en SQLite o PostgreSQL. El
directorio externo contiene la base SQLite cuando se usa ese motor, ajustes,
estado operativo y los pocos recursos que siguen siendo ficheros.

---

## Base de datos

La base de datos almacena todos los datos estructurados. Con SQLite es
`hub.db`; con PostgreSQL vive en el servidor indicado por `DATABASE_URL`.

| Tabla | Contenido |
|---|---|
| `users` | Cuentas de usuario — credenciales, rol y preferencias por usuario (tema, idioma); la política global puede imponer el tema sin borrar la preferencia personal |
| `accounts` | API keys de proveedor vinculadas por usuario (Anthropic, OpenAI, GitHub, Ollama, NVIDIA, Google), cada una con su propio `id` — se permiten varias cuentas del mismo proveedor; keys cifradas en reposo |
| `connections` | Conexiones de IA con nombre, selección de modelo y consumo acumulado de tokens — API keys cifradas en reposo |
| `knowledge_items` | Elementos de la base de conocimiento, incluidas sus labels de idioma |
| `conversations` | Historial de conversaciones (id, título, fechas) |
| `messages` | Mensajes individuales ligados a conversaciones |
| `agents`, `skills`, `prompts`, `tools` | Recursos reutilizables, propiedad, estado, contenido y metadatos |
| `tool_artifacts`, `tool_artifact_links`, `tool_version_artifacts` | Binarios de Tools por SHA-256, enlace activo y retención por versión |
| `resource_versions` | Historial inmutable de agentes, skills y Tools |
| `groups`, `resource_group_shares` | Multi-tenancy y acceso compartido |
| `resource_social` | Publicaciones visibles en Explorar |
| `workflows`, `llm_orchestrations` | Definiciones de ejecución y rutas LLM |

---

## Directorio de ficheros

| Ruta | Contenido |
|---|---|
| `memory/` | Memoria acumulada por cada agente entre conversaciones |
| `settings.json` | Ajustes de plataforma administrables y secretos locales |
| `centinel_state.json` | Estado operativo de Centinel |
| `agents/`, `skills/`, `connections/`, `accounts/` | Entradas legacy usadas solo durante migraciones; no son la fuente activa |

---

## Qué se versiona

Ningún dato de runtime se incluye en el repositorio. La base de datos y el directorio de datos contienen información específica de cada instalación.

---

## Skills

Las skills se almacenan en la base de datos con nombre, descripción, icono, una categoría del catálogo cerrado y su contenido. No admiten tags libres; sus labels se limitan al catálogo del sistema. Las skills públicas del sistema son de solo lectura; las creadas por usuarios conservan su propietario tanto si son privadas como públicas. El contenido se inyecta en el system prompt del agente cuando la skill está activada.

## Tools y artefactos

Los metadatos, instrucciones y scripts de una Tool viven en `tools`. Los
ejecutables nativos no se incluyen en listados ni detalles JSON: se guardan una
sola vez por SHA-256 en `tool_artifacts` y se relacionan con la Tool y sus
versiones. Por ello, una copia de seguridad restaurable debe incluir la base de
datos completa; copiar un supuesto directorio de Tools no conserva los
artefactos.

## Idioma del contenido

Los recursos textuales pueden llevar cero o varias labels `lang_*`: `lang_es`,
`lang_en`, `lang_fr`, `lang_de`, `lang_pt`, `lang_it`, `lang_zh`, `lang_ja` y
`lang_ar`. La ausencia de estas labels significa que el idioma no se ha
declarado o no aplica. En agentes, el campo heredado `language` se conserva por
compatibilidad y se sincroniza con la primera label de idioma; las labels son la
fuente canónica para catálogo y búsqueda. Los documentos enviados como
`multipart/form-data` aceptan las mismas labels mediante el campo `labels` en
formato JSON.
