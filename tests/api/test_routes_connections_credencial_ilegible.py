"""Una credencial que no se puede descifrar se ve y se dice (mejora #04).

El ciphertext no sale por la API ni se manda al proveedor: el listado marca la
conexión como `credentials_unreadable` y las acciones que usarían la clave
responden con el código `credential_unreadable`.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.storage import crypto

_CONN_PAYLOAD = {
    "type": "openai",
    "label": "OpenAI rota",
    "api_key": "sk-test-key",
    "model": "gpt-4o",
}


@pytest.fixture()
def rotar_clave(monkeypatch):
    """Simula el secreto de cifrado cambiado DESPUÉS de guardar la conexión,
    sin tocar el de firma del JWT (rotarlo de verdad invalidaría la sesión del
    propio test)."""

    def _rotar() -> None:
        monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))

    return _rotar


def test_listado_marca_la_conexion_y_no_filtra_el_ciphertext(
    admin_client, rotar_clave
):
    created = admin_client.post("/api/connections", json=_CONN_PAYLOAD).json()
    rotar_clave()

    conns = admin_client.get("/api/connections").json()
    rota = next(c for c in conns if c["id"] == created["id"])
    assert rota["credentials_unreadable"] is True
    assert rota["unreadable_fields"] == ["api_key"]
    assert "api_key" not in rota
    assert "enc:" not in str(rota)


def test_test_de_conexion_devuelve_el_codigo_propio(admin_client, rotar_clave):
    created = admin_client.post("/api/connections", json=_CONN_PAYLOAD).json()
    rotar_clave()

    r = admin_client.post(f"/api/connections/{created['id']}/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["code"] == "credential_unreadable"


def test_test_all_devuelve_el_codigo_propio(admin_client, rotar_clave):
    created = admin_client.post("/api/connections", json=_CONN_PAYLOAD).json()
    rotar_clave()

    r = admin_client.post("/api/connections/test-all", json={"ids": [created["id"]]})
    assert r.status_code == 200
    resultado = next(item for item in r.json() if item["id"] == created["id"])
    assert resultado["ok"] is False
    assert resultado["code"] == "credential_unreadable"


def test_import_models_se_niega_antes_de_llamar_al_proveedor(
    admin_client, rotar_clave
):
    created = admin_client.post("/api/connections", json=_CONN_PAYLOAD).json()
    rotar_clave()

    r = admin_client.post(f"/api/connections/{created['id']}/import-models")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "credential_unreadable"
