"""Cuentas de proveedor con la clave ilegible (mejora #04).

La cuenta sigue listándose —para que el usuario la vea y la arregle— pero
marcada, sin máscara de una clave que no existe, y las acciones que usarían la
credencial se niegan antes de llamar al proveedor.
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from app.storage import crypto


def _setup_user(client, username="accilegible"):
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


@pytest.fixture()
def rotar_clave(monkeypatch):
    def _rotar() -> None:
        monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))

    return _rotar


def _crear_cuenta(client) -> str:
    r = client.post(
        "/api/accounts",
        json={"provider": "openai", "api_key": "sk-test-openai-123456"},
    )
    assert r.status_code == 200
    return r.json()["id"]


def test_listado_marca_la_cuenta_y_no_enmascara_nada(client, rotar_clave):
    _setup_user(client, "accileg1")
    account_id = _crear_cuenta(client)
    rotar_clave()

    cuenta = next(
        a for a in client.get("/api/accounts").json() if a["id"] == account_id
    )
    assert cuenta["credentials_unreadable"] is True
    assert cuenta["api_key_masked"] == ""


def test_sync_se_niega_con_codigo_propio(client, rotar_clave):
    _setup_user(client, "accileg2")
    account_id = _crear_cuenta(client)
    rotar_clave()

    from unittest.mock import AsyncMock, patch

    with patch(
        "app.api.routes.accounts._fetch_models", new_callable=AsyncMock
    ) as fetch_models:
        r = client.post(f"/api/accounts/{account_id}/sync")

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "credential_unreadable"
    fetch_models.assert_not_awaited()


def test_test_con_clave_nueva_en_el_body_sigue_permitido(client, rotar_clave):
    """La guarda solo bloquea el uso de la clave guardada: probar una nueva
    antes de guardarla es justo la vía de recuperación."""
    from unittest.mock import AsyncMock, patch

    _setup_user(client, "accileg3")
    account_id = _crear_cuenta(client)
    rotar_clave()

    with patch(
        "app.api.routes.accounts._fetch_models",
        new_callable=AsyncMock,
        return_value=["gpt-4o"],
    ):
        r = client.post(
            f"/api/accounts/{account_id}/test", json={"api_key": "sk-nueva-123456"}
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_test_con_la_clave_guardada_ilegible_se_niega(client, rotar_clave):
    _setup_user(client, "accileg4")
    account_id = _crear_cuenta(client)
    rotar_clave()

    r = client.post(f"/api/accounts/{account_id}/test")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "credential_unreadable"
