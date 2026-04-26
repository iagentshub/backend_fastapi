<div align="center">
  <a href="index.md">← Documentación</a> &nbsp;·&nbsp;
  <a href="../en/tests.md">🇬🇧 Read in English</a>
</div>

<br>

<h1 align="center">Backend — Tests</h1>

---

## Requisitos

Las dependencias de test están en `requirements.txt`:

```
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Instalar todo de una vez:

```bash
pip install -r requirements.txt
```

---

## Ejecutar los tests

### Todos los tests (recomendado)

```bash
python3 rtests.py
```

`rtests.py` instala las dependencias automáticamente y lanza pytest con `--tb=short` y `-v` por defecto.

### Directamente con pytest

```bash
pytest
pytest -v          # verbose
pytest -q          # silencioso
```

---

## Filtrado

```bash
# Ejecutar un subdirectorio concreto
python3 rtests.py tests/api/
python3 rtests.py tests/storage/

# Filtrar por nombre de test (coincidencia parcial)
python3 rtests.py -k auth
python3 rtests.py -k "register or admin"

# Ejecutar un único fichero
pytest tests/api/test_routes_auth.py

# Ejecutar un único test
pytest tests/api/test_routes_auth.py::test_login_ok
```

---

## Estructura de tests

```
tests/
  conftest.py                    ← fixtures compartidos
  test_auth.py                   ← tests unitarios del módulo auth
  api/
    test_routes_auth.py          ← POST /api/auth/login|logout
    test_routes_register.py      ← POST /api/auth/register
    test_routes_me.py            ← GET /api/auth/me, change-password
    test_routes_admin.py         ← GET/DELETE /api/admin/users
    test_routes_agents.py        ← CRUD /api/agents
    test_routes_connections.py   ← CRUD /api/connections
    test_routes_memory.py        ← CRUD /api/memory
    test_routes_skills.py        ← CRUD /api/skills
  connections/
    test_base.py                 ← registro de providers, FieldDef, TestResult
    test_openai.py
    test_anthropic.py
    test_google.py
    test_grok.py
    test_qwen.py
    test_ollama.py
  services/
    test_chat.py                 ← routing de proveedores, SSE, manejo de errores
  storage/
    test_agent_storage.py
    test_connection_storage.py
    test_skill_storage.py
    test_memory_storage.py
```

---

## Fixtures (`conftest.py`)

| Fixture | Alcance | Descripción |
|---|---|---|
| `tmp_data_dir` | sesión | Directorio de datos temporal aislado con settings de admin por defecto |
| `patch_data_dir` | función (autouse) | Redirige `GAIA_DATA_DIR` y todas las rutas de storage a `tmp_data_dir` |
| `client` | función | `TestClient` de FastAPI con datos aislados |
| `admin_client` | función | `client` ya autenticado como admin |
| `reset_rate_limiter` | función | Limpia el rate limiter en memoria entre tests |

---

## Notas

- **Tests de providers** (`tests/connections/`) mockean `urllib.request.urlopen` — no se hacen llamadas reales a las APIs.
- **Tests de rate limiter** necesitan el fixture `reset_rate_limiter` para evitar interferencias entre ejecuciones.
- **`asyncio_mode = auto`** está configurado en `pytest.ini` para que los tests async no necesiten decoradores extra.
- Los tests están completamente aislados: cada función de test recibe un directorio de datos limpio.
