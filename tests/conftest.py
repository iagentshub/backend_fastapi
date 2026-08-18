"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

# Red de seguridad: algunos módulos de test importan app.auth.auth / app.config.data
# a nivel de módulo (ej. "from app.auth.auth import create_token"), lo que dispara la
# primera importación de app.config.data durante la FASE DE COLECCIÓN de pytest —
# antes de que corra ningún fixture. Si GAIA_DATA_DIR no está ya fijado en ese momento,
# DATA_DIR (y cualquier nombre importado por valor, ej. auth.py:DATA_DIR) queda
# "congelado" apuntando al path por defecto real (hermano del repo, o el que indique
# GAIA_DATA_DIR en el entorno/.env del desarrollador), y ningún monkeypatch posterior
# lo corrige. Se sobrescribe SIEMPRE (no setdefault): los tests nunca deben depender
# del entorno ambiente del desarrollador, ni siquiera si algún día se configura
# GAIA_DATA_DIR ahí para conveniencia del servidor de desarrollo.
os.environ["GAIA_DATA_DIR"] = tempfile.mkdtemp(prefix="gaia_test_collection_")
os.environ["DATABASE_URL"] = ""

# bcrypt con las 12 rondas de producción cuesta ~235 ms por hash, y la suite
# tiene 141 puntos de registro de usuario: tests/auth se pasaba el 91% de su
# tiempo (17,7 s de 19,5 s) esperando a bcrypt sin probar nada. 4 es el mínimo
# que admite bcrypt. En producción el default sigue siendo 12 — ver el suelo y
# el techo en app/config/session.py.
os.environ["GAIA_BCRYPT_ROUNDS"] = "4"

import pytest
from fastapi.testclient import TestClient


def pytest_addoption(parser):
    parser.addoption(
        "--actualizar-contrato",
        action="store_true",
        default=False,
        help="Reescribe tests/api/contrato_rutas.txt con la superficie actual de la API.",
    )


@pytest.fixture(scope="session")
def tmp_data_dir():
    """Creates an isolated data directory for all tests."""
    d = Path(tempfile.mkdtemp(prefix="gaia_test_"))
    (d / "connections").mkdir()
    (d / "agents").mkdir()
    (d / "skills").mkdir()
    (d / "memory").mkdir()
    (d / "settings.json").write_text(
        json.dumps({"jwt_secret": "test-secret-key-for-tests-only-min-32-bytes-long"}),
        encoding="utf-8",
    )
    (d / "connections" / "connections.json").write_text("[]", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def patch_data_dir(tmp_data_dir, tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to the temp directory before each test.

    Uses a per-test SQLite DB file (via tmp_path) so tests don't share state,
    while keeping shared fixtures (settings.json, etc.) from tmp_data_dir.
    Forces SQLite mode (DATABASE_URL='').
    """
    db_file = tmp_path / "hub.db"
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_data_dir))
    (tmp_data_dir / "settings.json").write_text(
        json.dumps({"jwt_secret": "test-secret-key-for-tests-only-min-32-bytes-long"}),
        encoding="utf-8",
    )

    # Patch config module attrs — _cfg.DB_FILE is read dynamically by app lifespan
    import app.config.data as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(cfg, "DB_FILE", db_file)
    monkeypatch.setattr(
        cfg, "CONN_FILE", tmp_data_dir / "connections" / "connections.json"
    )
    monkeypatch.setattr(cfg, "AGENTS_DIR", tmp_data_dir / "agents")
    monkeypatch.setattr(cfg, "SKILLS_DIR", tmp_data_dir / "skills")
    monkeypatch.setattr(cfg, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    # Force SQLite mode and initialize the per-test DB BEFORE importing routes
    import app.storage.db as db_mod

    monkeypatch.setattr(db_mod, "IS_PG", False)
    monkeypatch.setattr(db_mod, "PH", "?")

    old_sqlite_path = db_mod._sqlite_path
    old_pg_pool = db_mod._pg_pool

    asyncio.run(db_mod.init_db(db_file))

    # Patch auth module paths
    import app.auth.auth as auth_mod

    # auth.py importa DATA_DIR por valor ("from app.config.data import ... DATA_DIR ..."),
    # así que parchear solo cfg.DATA_DIR no basta — auth_mod.DATA_DIR es el binding real
    # que usa _pass_file = DATA_DIR / ".admin_pass" (sin esto, .admin_pass se escribía
    # en el path real por defecto en cada test, ver conftest.py:12-21).
    monkeypatch.setattr(auth_mod, "DATA_DIR", tmp_data_dir)

    # SETTINGS_FILE (igual trampa que arriba) vive ahora en app.auth.passwords,
    # de donde auth.py ya no lo reexporta por valor.
    import app.auth.passwords as passwords_mod

    monkeypatch.setattr(passwords_mod, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    # Patch MemoryStorage live instances
    from app.storage.memory_storage import MemoryStorage

    isolated_memory = MemoryStorage(memory_dir)
    import app.api.routes.agent_chat as agent_chat_routes
    import app.api.routes.agent_exports as agent_export_routes
    import app.api.routes.agents as agents_routes
    import app.api.routes.memory as memory_routes

    monkeypatch.setattr(memory_routes, "_storage", isolated_memory)
    monkeypatch.setattr(agents_routes, "_memory", isolated_memory)
    monkeypatch.setattr(agent_chat_routes, "_memory", isolated_memory)
    monkeypatch.setattr(agent_export_routes, "_memory", isolated_memory)

    # Patch el binding local de AGENTS_DIR en agents.py — se importa a nivel
    # de módulo con "from app.config.data import AGENTS_DIR", por lo que
    # parchear solo cfg.AGENTS_DIR no es suficiente para que _apply_locale
    # encuentre los archivos de locale del test.
    monkeypatch.setattr(agents_routes, "AGENTS_DIR", tmp_data_dir / "agents")
    monkeypatch.setattr(agent_chat_routes, "AGENTS_DIR", tmp_data_dir / "agents")
    monkeypatch.setattr(agent_export_routes, "AGENTS_DIR", tmp_data_dir / "agents")

    # Forzar REGISTRATION_MODE="open" en los tests para que client.post("/api/auth/register")
    # funcione independientemente del entorno de producción (GAIA_REGISTRATION=closed/invite).
    # auth.py es un paquete: REGISTRATION_MODE/EMAIL_VERIFY_ENABLED/SECURE_COOKIES
    # se importan por valor en el submódulo login.py, no en __init__.py.
    import app.api.routes.auth.login as auth_routes

    monkeypatch.setattr(auth_routes, "REGISTRATION_MODE", "open")

    # Forzar EMAIL_VERIFY_ENABLED=False para que el registro no quede en "pending"
    # cuando GAIA_EMAIL_VERIFY=true en producción. Los tests que necesiten True
    # lo parchean ellos mismos con unittest.mock.patch.
    monkeypatch.setattr(auth_routes, "EMAIL_VERIFY_ENABLED", False)
    import app.auth.auth as auth_mod

    monkeypatch.setattr(auth_mod, "EMAIL_VERIFY_ENABLED", False)

    # Limpiar sesiones guest y forzar MAX_SESSIONS=200 antes de cada test:
    # - En producción GAIA_MAX_GUEST_SESSIONS puede ser 0 (guest desactivado),
    #   lo que hace que get_session() lance 503 para CUALQUIER sesión nueva.
    # - Limpiar _sessions evita que las sesiones se acumulen entre tests.
    import app.storage.guest as guest_mod

    guest_mod._sessions.clear()
    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 200)

    # Forzar SECURE_COOKIES=False: con Secure=True las cookies no se almacenan
    # en el TestClient (HTTP), lo que provoca 401 en todas las llamadas siguientes.
    # Vive en app.auth.cookies desde que la sesión pasó a ser dos cookies (la de
    # sesión y su token anti-CSRF) y las emite un único helper; los ocho handlers
    # que llamaban a set_cookie ya no importan el valor.
    import app.auth.cookies as cookies_mod

    monkeypatch.setattr(cookies_mod, "SECURE_COOKIES", False)

    # licenses.py importa SETTINGS_FILE por valor, igual que auth.py (ver arriba).
    # El binding se fija cuando se importa el módulo: si algo lo importa durante
    # la COLECCIÓN de pytest —basta con que un fichero de test lo importe a nivel
    # superior— queda apuntando al directorio de colección (conftest.py:22) en vez
    # de a tmp_data_dir, y _billing_enabled() lee un settings.json que no existe:
    # la puerta de licencias no se activa nunca y los tests que esperan 403 pasan
    # a recibir 200. Parchearlo aquí lo hace independiente del orden de import.
    import app.middleware.licenses as licenses_mod

    monkeypatch.setattr(licenses_mod, "SETTINGS_FILE", tmp_data_dir / "settings.json")

    # Además, el caché de billing_enabled arranca limpio: varios tests escriben
    # settings.json a mano, saltándose _write_platform_cfg (que es quien invalida).
    licenses_mod.invalidate_billing_cache()

    yield

    # Reset DB state after test
    asyncio.run(db_mod.close_db_pool())
    db_mod._sqlite_path = old_sqlite_path
    db_mod._pg_pool = old_pg_pool


def _echo_csrf_cookie(request) -> None:
    """Manda `X-CSRF-Token`, que es lo que hacen React y Flutter.

    Se **deriva del `ga_token` de la petición** en vez de copiar la cookie
    `ga_csrf`, y eso no es un atajo: el valor es el mismo —el servidor emite
    `derive_csrf_token(ga_token)`— y así da igual cómo haya montado la sesión
    el test. 43 ficheros inyectan el JWT con `client.cookies.set("ga_token",
    …)` sin pasar por `/login`, así que nunca llegan a tener la cookie.

    No pisa una cabecera ya puesta: los tests del middleware necesitan mandar
    un token deliberadamente malo.
    """
    if "x-csrf-token" in request.headers:
        return
    # El MISMO parser que usa Starlette, no uno propio: varios tests acaban con
    # dos cookies `ga_token` en el jar (una puesta a mano sin dominio y otra que
    # llegó por Set-Cookie con dominio), y ahí importa cuál gana. Starlette se
    # queda con la última; un bucle escrito aquí cogía la primera y el token
    # salía firmado con un JWT que el servidor no estaba mirando.
    from starlette.requests import cookie_parser

    ga_token = cookie_parser(request.headers.get("cookie", "")).get("ga_token")
    if ga_token:
        from app.auth.passwords import derive_csrf_token

        request.headers["x-csrf-token"] = derive_csrf_token(ga_token)


@pytest.fixture(autouse=True)
def csrf_en_todo_testclient(monkeypatch):
    """Instala el hook en CUALQUIER TestClient, no solo en el del fixture.

    Se parchea el constructor en vez de añadirlo cliente a cliente porque una
    docena de tests montan el suyo (`TestClient(client.app)`) para hablar como
    un segundo usuario. Hacerlo uno a uno deja el fallo esperando al siguiente
    que lo haga: sin la cabecera, sus mutaciones son 403 y el mensaje no dice
    nada de CSRF, solo que la petición no llegó.
    """
    original = TestClient.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.event_hooks["request"].append(_echo_csrf_cookie)

    monkeypatch.setattr(TestClient, "__init__", init)


@pytest.fixture()
def client(patch_data_dir):
    """TestClient for FastAPI with isolated data."""
    from app.api.app import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def admin_client(client, patch_data_dir):
    """Client authenticated as admin user."""
    from app.auth.auth import create_token, get_user_by_username, register_user
    from app.storage.db import open_db

    async def _setup():
        if not await get_user_by_username("testadmin"):
            await register_user("testadmin", "pass1234", email="testadmin@example.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                ("admin", "testadmin"),
            )
            await conn.commit()

    asyncio.run(_setup())
    token = create_token("testadmin")
    client.cookies.set("ga_token", token)
    # La sesión son dos cookies: este cliente se salta /login, así que la
    # segunda hay que derivarla igual que lo haría set_session_cookies.
    from app.auth.passwords import derive_csrf_token

    client.cookies.set("ga_csrf", derive_csrf_token(token))
    return client


@pytest.fixture(autouse=True)
def clear_all_rate_limiters():
    """Limpia el estado de TODOS los rate limiters antes y después de cada test.

    Los limiters son singletons a nivel de módulo y comparten IP entre tests
    (TestClient siempre usa 'testclient' como peer). Sin limpiar, el 6.º test
    que llama a /api/auth/guest recibe 429 porque _guest_limiter (límite=5) ya
    está saturado.

    Se recorren los limiters que RateLimiter registra al construirse en vez de
    listarlos aquí: la lista escrita a mano se había quedado sin 4 de los 13
    (device flow, login de GitHub, sync del hub y social), y cada limiter nuevo
    volvía a olvidarse en silencio.

    Los limiters compartidos no cuentan en `_data` sino en la tabla
    `rate_limit_windows`, y esa no hace falta vaciarla aquí: `patch_data_dir` da
    una BD SQLite nueva por test. Si algún día un test reutilizara la BD, el
    aislamiento de estos se perdería sin que este fixture se enterase.
    """

    def _clear():
        from app.middleware.ratelimit import INSTANCES

        for lim in INSTANCES:
            lim._data.clear()

    _clear()
    yield
    _clear()


@pytest.fixture()
def reset_rate_limiter():
    """Alias de compatibilidad — clear_all_rate_limiters ya es autouse."""
    yield
