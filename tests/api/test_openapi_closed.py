"""El esquema OpenAPI solo se publica en modo desarrollo.

/openapi.json describe la superficie entera de la API (rutas, cuerpos, nombres
de campo). Estaba abierto en producción porque create_app() gateaba docs_url
pero no openapi_url.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_esquema_cerrado_por_defecto(client):
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_esquema_abierto_en_modo_desarrollo(patch_data_dir, monkeypatch):
    monkeypatch.setenv("GAIA_DEV_MODE", "true")
    from app.api.app import create_app

    with TestClient(create_app()) as c:
        esquema = c.get("/openapi.json")
        assert esquema.status_code == 200
        assert "paths" in esquema.json()
        assert c.get("/docs").status_code == 200
