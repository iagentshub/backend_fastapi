<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/tests.md">🇪🇸 Ver en Español</a>
</div>

<br>

<h1 align="center">Backend — Tests</h1>

---

## Requirements

Test dependencies are listed in `requirements.txt`:

```
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Install everything at once:

```bash
pip install -r requirements.txt
```

---

## Running Tests

### All tests (recommended)

```bash
python3 rtests.py
```

`rtests.py` installs dependencies automatically and runs pytest with `--tb=short` and `-v` by default.

### Directly with pytest

```bash
pytest
pytest -v          # verbose
pytest -q          # quiet
```

---

## Filtering

```bash
# Run a specific directory
python3 rtests.py tests/api/
python3 rtests.py tests/storage/

# Filter by test name (substring match)
python3 rtests.py -k auth
python3 rtests.py -k "register or admin"

# Run a single file
pytest tests/api/test_routes_tools.py

# Run a single test
pytest tests/api/test_routes_tools.py::test_save_private_tool
```

---

## Test Structure

```
tests/
  conftest.py                    ← shared fixtures
  api/
    test_routes_register.py      ← POST /api/auth/register
    test_routes_me.py            ← GET /api/auth/me, change-password
    test_routes_agents.py        ← CRUD /api/agents
    test_routes_connections.py   ← CRUD /api/connections
    test_routes_skills.py        ← CRUD /api/skills
    test_routes_tools.py         ← Tool CRUD, binaries, and versions
    test_resource_management.py  ← activate, deactivate, and restore resources
    admin/                       ← admin-only endpoints
  connections/
    test_base.py                 ← provider registry, FieldDef, TestResult
  services/
    chat/                        ← routing, SSE, memory, and injection
    test_tool_policy.py          ← Tool review, quarantine, and consumption
  storage/
    test_agent_storage.py
    test_connection_storage.py
    test_skill_storage.py
    test_memory_storage.py
    test_tool_storage.py
```

The suite grows with the product. This tree lists representative areas rather
than a closed inventory; use `pytest --collect-only -q` to inspect the current
checkout.

---

## Fixtures (`conftest.py`)

| Fixture | Scope | Description |
|---|---|---|
| `tmp_data_dir` | session | Isolated temp data directory with default admin settings |
| `patch_data_dir` | function (autouse) | Redirects `GAIA_DATA_DIR` and all storage paths to `tmp_data_dir` |
| `client` | function | FastAPI `TestClient` with isolated data |
| `admin_client` | function | `client` already authenticated as admin |
| `reset_rate_limiter` | function | Clears the in-memory rate limiter between tests |

---

## Notes

- **Provider tests** (`tests/connections/`) mock `urllib.request.urlopen` — no real API calls are made.
- **Rate limiter tests** require the `reset_rate_limiter` fixture to avoid interference between test runs.
- **`asyncio_mode = auto`** is set in `pytest.ini` so async tests run without extra decorators.
- Tests are fully isolated: each test function gets a clean data directory.
