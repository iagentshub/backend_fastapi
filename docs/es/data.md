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
| `users` | Cuentas de usuario — credenciales, rol y preferencias por usuario (tema, idioma); la política global puede imponer el tema sin borrar la preferencia personal |
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

Las skills se almacenan en la base de datos con nombre, descripción, icono, una categoría del catálogo cerrado y su contenido. No admiten tags libres; sus labels se limitan al catálogo del sistema. Las skills públicas del sistema son de solo lectura; las creadas por usuarios conservan su propietario tanto si son privadas como públicas. El contenido se inyecta en el system prompt del agente cuando la skill está activada.
