"""Frontera de las consultas de borrado que no filtran por propietario.

Estas cuatro consultas son deliberadamente administrativas. El filtro de
tenant no vive en el SQL, sino en el llamante que decide pasar ``owner_id=None``
o ``allow_public=True``. Por eso no basta con probar el almacenamiento: este
fichero fija la frontera HTTP completa y comprueba además qué consulta llegó a
ejecutarse.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from fastapi.testclient import TestClient

APP = Path(__file__).parents[2] / "app"

# Si aparece otro llamante, hay que decidir aquí de forma explícita por qué
# tiene autoridad administrativa. Un uso nuevo no entra en silencio.
LLAMANTES_ADMIN_PERMITIDOS = {
    ("api/routes/admin/resources.py", "admin_delete_agent"),
    ("api/routes/admin/resources.py", "admin_delete_connection"),
    ("api/routes/admin/resources.py", "admin_delete_prompt"),
    ("api/routes/agents.py", "delete_agent"),
    ("api/routes/connections.py", "delete_connection"),
    ("api/routes/prompts.py", "delete_prompt"),
}

CONSULTAS_SIN_PROPIETARIO_PERMITIDAS = {
    ("storage/agent_storage.py", "_delete", "queries/agents:delete_any"),
    ("storage/agent_storage.py", "_delete", "queries/agents:delete_not_public"),
    (
        "storage/connection_storage.py",
        "_delete",
        "queries/connections:delete_any",
    ),
    ("storage/prompt_storage.py", "_delete", "queries/prompts:delete_scoped"),
}


class _LlamadasAdmin(ast.NodeVisitor):
    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        self.funciones: list[str] = []
        self.encontradas: set[tuple[str, str]] = set()
        self.consultas: set[tuple[str, str, str]] = set()

    def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:
        self._visitar_funcion(nodo)

    def visit_AsyncFunctionDef(self, nodo: ast.AsyncFunctionDef) -> None:
        self._visitar_funcion(nodo)

    def _visitar_funcion(self, nodo: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.funciones.append(nodo.name)
        self.generic_visit(nodo)
        self.funciones.pop()

    def visit_Call(self, nodo: ast.Call) -> None:
        ruta_relativa = self.ruta.relative_to(APP).as_posix()
        if (
            isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "delete_as_admin"
            and self.funciones
        ):
            self.encontradas.add((ruta_relativa, self.funciones[-1]))
        if (
            isinstance(nodo.func, ast.Name)
            and nodo.func.id == "sql"
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
            and nodo.args[0].value
            in {consulta[2] for consulta in CONSULTAS_SIN_PROPIETARIO_PERMITIDAS}
            and self.funciones
        ):
            self.consultas.add(
                (ruta_relativa, self.funciones[-1], str(nodo.args[0].value))
            )
        self.generic_visit(nodo)


def test_delete_as_admin_solo_se_llama_desde_la_lista_autorizada():
    encontradas: set[tuple[str, str]] = set()
    for ruta in APP.rglob("*.py"):
        visitante = _LlamadasAdmin(ruta)
        visitante.visit(ast.parse(ruta.read_text(encoding="utf-8")))
        encontradas.update(visitante.encontradas)

    assert encontradas == LLAMANTES_ADMIN_PERMITIDOS


def test_las_consultas_sin_propietario_siguen_encapsuladas():
    encontradas: set[tuple[str, str, str]] = set()
    for ruta in APP.rglob("*.py"):
        visitante = _LlamadasAdmin(ruta)
        visitante.visit(ast.parse(ruta.read_text(encoding="utf-8")))
        encontradas.update(visitante.consultas)

    assert encontradas == CONSULTAS_SIN_PROPIETARIO_PERMITIDAS


def test_los_borrados_normales_exigen_propietario():
    from app.storage.agent_storage import AgentStorage
    from app.storage.connection_storage import ConnectionStorage
    from app.storage.prompt_storage import PromptStorage

    for clase in (AgentStorage, ConnectionStorage, PromptStorage):
        parametro = inspect.signature(clase.delete).parameters["owner_id"]
        assert parametro.default is inspect.Parameter.empty, (
            f"{clase.__name__}.delete ha vuelto a permitir omitir owner_id"
        )


def _registrar(nombre: str) -> str:
    from app.auth.auth import get_user_by_username, register_user

    async def crear() -> str:
        await register_user(nombre, "pass1234", email=f"{nombre}@test.com")
        usuario = await get_user_by_username(nombre)
        assert usuario is not None
        return str(usuario["id"])

    return asyncio.run(crear())


def _actuar_como(client: TestClient, nombre: str) -> None:
    from app.auth.auth import create_token

    client.cookies.set("ga_token", create_token(nombre))


def _vigilar_consulta(
    monkeypatch, modulo: ModuleType, consulta_peligrosa: str
) -> list[str]:
    original: Callable[[str], str] = modulo.sql
    ejecutadas: list[str] = []

    def vigilada(nombre: str) -> str:
        if nombre == consulta_peligrosa:
            ejecutadas.append(nombre)
        return original(nombre)

    monkeypatch.setattr(modulo, "sql", vigilada)
    return ejecutadas


def _volver_a_admin(client: TestClient) -> None:
    _actuar_como(client, "testadmin")


def test_agents_delete_any_solo_se_alcanza_desde_admin(admin_client, monkeypatch):
    import app.api.routes.agents as rutas
    import app.storage.agent_storage as modulo

    victima_id = _registrar("frontera_agent_any_victima")
    _registrar("frontera_agent_any_atacante")
    agente = asyncio.run(
        rutas._agents.save(
            {"name": "Agente ajeno para delete_any"}, owner_id=victima_id
        )
    )
    ejecutadas = _vigilar_consulta(monkeypatch, modulo, "queries/agents:delete_any")

    _actuar_como(admin_client, "frontera_agent_any_atacante")
    respuesta = admin_client.delete(f"/api/admin/agents/{agente['id']}?scope=private")
    assert respuesta.status_code == 403
    assert ejecutadas == []
    assert asyncio.run(rutas._agents.get(agente["id"])) is not None

    _volver_a_admin(admin_client)
    respuesta = admin_client.delete(f"/api/admin/agents/{agente['id']}?scope=private")
    assert respuesta.status_code == 200, respuesta.text
    assert ejecutadas == ["queries/agents:delete_any"]


def test_agents_delete_not_public_solo_se_usa_en_la_rama_admin(
    admin_client, monkeypatch
):
    import app.api.routes.agents as rutas
    import app.storage.agent_storage as modulo

    victima_id = _registrar("frontera_agent_private_victima")
    _registrar("frontera_agent_private_atacante")
    agente = asyncio.run(
        rutas._agents.save(
            {"name": "Agente ajeno para delete_not_public"}, owner_id=victima_id
        )
    )
    ejecutadas = _vigilar_consulta(
        monkeypatch, modulo, "queries/agents:delete_not_public"
    )

    _actuar_como(admin_client, "frontera_agent_private_atacante")
    respuesta = admin_client.delete(f"/api/agents/{agente['id']}")
    assert respuesta.status_code in (403, 404)
    assert ejecutadas == []
    assert asyncio.run(rutas._agents.get(agente["id"])) is not None

    _volver_a_admin(admin_client)
    respuesta = admin_client.delete(f"/api/agents/{agente['id']}")
    assert respuesta.status_code == 200, respuesta.text
    assert ejecutadas == ["queries/agents:delete_not_public"]


def test_connections_delete_any_solo_se_alcanza_desde_admin(admin_client, monkeypatch):
    import app.storage.connection_storage as modulo

    victima_id = _registrar("frontera_connection_victima")
    _registrar("frontera_connection_atacante")
    storage = modulo.ConnectionStorage()
    conexion = asyncio.run(
        storage.save(
            {"name": "Conexión ajena", "type": "openai", "api_key": "sk-test"},
            owner_id=victima_id,
        )
    )
    ejecutadas = _vigilar_consulta(
        monkeypatch, modulo, "queries/connections:delete_any"
    )

    _actuar_como(admin_client, "frontera_connection_atacante")
    respuesta = admin_client.delete(f"/api/connections/{conexion['id']}")
    assert respuesta.status_code in (403, 404)
    respuesta_admin = admin_client.delete(f"/api/admin/connections/{conexion['id']}")
    assert respuesta_admin.status_code == 403
    assert ejecutadas == []
    assert asyncio.run(storage.get(conexion["id"], owner_id=victima_id)) is not None

    _volver_a_admin(admin_client)
    respuesta = admin_client.delete(f"/api/admin/connections/{conexion['id']}")
    assert respuesta.status_code == 200, respuesta.text
    assert ejecutadas == ["queries/connections:delete_any"]


def test_prompts_delete_scoped_solo_se_usa_en_la_rama_admin(admin_client, monkeypatch):
    import app.storage.prompt_storage as modulo

    victima_id = _registrar("frontera_prompt_victima")
    _registrar("frontera_prompt_atacante")
    storage = modulo.PromptStorage()
    prompt = asyncio.run(
        storage.save(
            "private",
            {
                "name": "Prompt ajeno",
                "alias": "prompt-ajeno-frontera",
                "content": "No borrar fuera de la frontera admin",
            },
            owner_id=victima_id,
        )
    )
    ejecutadas = _vigilar_consulta(monkeypatch, modulo, "queries/prompts:delete_scoped")

    _actuar_como(admin_client, "frontera_prompt_atacante")
    respuesta = admin_client.delete(f"/api/prompts/private/{prompt['id']}")
    assert respuesta.status_code in (403, 404)
    assert ejecutadas == []
    assert asyncio.run(storage.get_any(prompt["id"], owner_id=victima_id)) is not None

    _volver_a_admin(admin_client)
    respuesta = admin_client.delete(f"/api/prompts/private/{prompt['id']}")
    assert respuesta.status_code == 200, respuesta.text
    assert ejecutadas == ["queries/prompts:delete_scoped"]
