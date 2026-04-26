<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/architecture.md">🇬🇧 Read in English</a>
</div>

<br>

# Arquitectura

El backend sigue una arquitectura por capas con separación clara de responsabilidades:

```
Petición → Router → Service → Storage → Sistema de ficheros
               ↓
          Middleware de auth (JWT)
```

| Capa | Ruta | Responsabilidad |
|---|---|---|
| **Routers** | `app/api/routes/` | Handlers HTTP, validación de requests, formato de respuesta |
| **Services** | `app/services/` | Lógica de negocio, orquestación de proveedores, streaming |
| **Storage** | `app/storage/` | Persistencia en sistema de ficheros (JSON, Markdown) |
| **Connections** | `app/connections/` | Adaptadores de proveedores (Anthropic, OpenAI, Google, Grok, Qwen, Ollama) |
| **Auth** | `app/auth/` | Creación y validación de JWT, hashing de contraseñas |
| **Config** | `app/config/` | Configuración por variables de entorno (servidor, datos, CORS, JWT) |

---

## Estructura del código

```
app/
  api/
    app.py          ← factoría FastAPI (create_app)
    routes/         ← auth, agentes, skills, memoria, conexiones
  auth/             ← helpers JWT, gestión multi-usuario
  config/
    server.py       ← host, puerto, reload
    data.py         ← rutas del directorio de datos
    cors.py         ← orígenes CORS permitidos
    jwt.py          ← algoritmo, expiración y validación del secreto
  connections/      ← adaptadores de proveedores (Anthropic, OpenAI, Google, Grok, Qwen, Ollama)
  models/           ← esquemas Pydantic
  services/         ← lógica de negocio (streaming de chat)
  storage/          ← capa de persistencia en sistema de ficheros
main.py             ← punto de entrada (uvicorn)
requirements.txt
Dockerfile
tests/              ← suite de tests
```

---

## Decisiones de diseño clave

- **Servicio sin estado** — todo el estado vive en el directorio de datos montado (`GAIA_DATA_DIR`). El contenedor en sí no guarda estado.
- **Almacenamiento en sistema de ficheros** — agentes, conexiones, memoria y skills se guardan como ficheros JSON/Markdown. No se necesita base de datos.
- **Streaming SSE** — las respuestas del chat se envían por streaming usando `text/event-stream` via `asyncio.to_thread` para no bloquear el event loop.
- **Cookies HTTP-only** — los tokens de sesión nunca quedan expuestos a JavaScript, reduciendo el riesgo de XSS.
