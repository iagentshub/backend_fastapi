<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/architecture.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Architecture

The backend follows a layered architecture with clear separation of concerns:

```
Request → Router → Service → Storage → File System
                ↓
           Auth middleware (JWT)
```

| Layer | Path | Responsibility |
|---|---|---|
| **Routers** | `app/api/routes/` | HTTP handlers, request validation, response shaping |
| **Services** | `app/services/` | Business logic, provider orchestration, streaming |
| **Storage** | `app/storage/` | File-system persistence (JSON, Markdown) |
| **Connections** | `app/connections/` | Provider adapters (Anthropic, OpenAI, Google, Grok, Qwen, Ollama) |
| **Auth** | `app/auth/` | JWT creation, validation, and password hashing |
| **Config** | `app/config/` | Environment-variable driven configuration (server, data, CORS, JWT) |

---

## Code Structure

```
app/
  api/
    app.py          ← FastAPI factory (create_app)
    routes/         ← auth, agents, skills, memory, connections
  auth/             ← JWT helpers, multi-user management
  config/
    server.py       ← host, port, reload
    data.py         ← data directory paths
    cors.py         ← allowed CORS origins
    jwt.py          ← algorithm, expiry, and secret validation
  connections/      ← provider adapters (Anthropic, OpenAI, Google, Grok, Qwen, Ollama)
  models/           ← Pydantic schemas
  services/         ← business logic (chat streaming)
  storage/          ← file-system persistence layer
main.py             ← entry point (uvicorn)
requirements.txt
Dockerfile
tests/              ← test suite
```

---

## Key Design Decisions

- **Stateless service** — all state lives in the mounted data directory (`GAIA_DATA_DIR`). The container itself holds no state.
- **File-system storage** — agents, connections, memory and skills are stored as JSON/Markdown files. No database required.
- **SSE streaming** — chat responses are streamed using `text/event-stream` via `asyncio.to_thread` to avoid blocking the event loop.
- **HTTP-only cookies** — session tokens are never exposed to JavaScript, reducing XSS risk.
