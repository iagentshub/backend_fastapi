"""Test del handler catch-all @app.exception_handler(Exception) en app.py.

Verifica que una excepción no controlada dentro de una dependency (require_auth,
antes de llegar al cuerpo de cualquier ruta) responde con el contrato JSON
estándar de la API en vez del 500 en texto plano de Starlette.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.auth import create_token, register_user


def test_excepcion_no_controlada_en_dependency_devuelve_json_500(client, monkeypatch):
    import asyncio

    import app.api.routes.auth.dependencies as deps

    asyncio.run(register_user("excboom", "pass1234", email="excboom@example.com"))

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("fallo simulado de BD")

    monkeypatch.setattr(deps, "get_user_by_identity", _boom)

    no_raise_client = TestClient(client.app, raise_server_exceptions=False)
    no_raise_client.cookies.set("ga_token", create_token("excboom"))

    r = no_raise_client.get("/api/users/excboom")

    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "internal_error"
