"""Consentimiento legal versionado: alta, reaceptación y puertas de acceso."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient


def _document(document_type: str, version: str, locale: str = "es") -> dict[str, str]:
    return {
        "document_type": document_type,
        "version": version,
        "content_sha256": ("a" if document_type == "terms" else "b") * 64,
        "document_url": f"/legal/{document_type}/{locale}/{version}",
    }


def _legal(version: str = "2026-09-01", *, required: bool = True) -> dict:
    documents = {}
    for document_type in ("terms", "privacy"):
        documents[document_type] = {
            "version": version,
            "locales": {
                locale: {
                    "url": f"/legal/{document_type}/{locale}/{version}",
                    "sha256": ("a" if document_type == "terms" else "b") * 64,
                }
                for locale in ("es", "en")
            },
        }
    return {
        "required": required,
        "ready": True,
        "accept_url": "/app/legal-acceptance",
        "documents": documents,
    }


def _write_legal(tmp_data_dir, legal: dict) -> None:
    from app.services.platform_settings import invalidate_platform_cfg_cache

    settings_file = tmp_data_dir / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "jwt_secret": "test-secret-key-for-tests-only-min-32-bytes-long",
                "legal": legal,
            }
        ),
        encoding="utf-8",
    )
    invalidate_platform_cfg_cache()


def _acceptance(version: str = "2026-09-01", locale: str = "es") -> dict:
    return {
        "accepted": True,
        "locale": locale,
        "documents": [
            _document("terms", version, locale),
            _document("privacy", version, locale),
        ],
    }


def _register(client: TestClient, username: str, *, acceptance: dict | None = None):
    body = {
        "username": username,
        "email": f"{username}@test.com",
        "password": "pass1234",
    }
    if acceptance is not None:
        body["legal_acceptance"] = acceptance
    return client.post("/api/auth/register", json=body)


def _rows() -> list[dict]:
    from app.storage.db import open_db

    async def fetch() -> list[dict]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT document_type, version, locale, content_sha256, "
                "document_url, accepted_at, source FROM legal_acceptances "
                "ORDER BY document_type, version"
            )
            return [dict(row) for row in rows]

    return asyncio.run(fetch())


def test_publica_contrato_legal_vigente(client, tmp_data_dir):
    _write_legal(tmp_data_dir, _legal())

    response = client.get("/api/settings/platform/public")

    assert response.status_code == 200
    assert response.json()["legal"] == _legal()


def test_registro_exige_y_persiste_aceptacion_en_la_misma_alta(client, tmp_data_dir):
    _write_legal(tmp_data_dir, _legal())

    missing = _register(client, "legal_missing")
    assert missing.status_code == 428
    assert missing.json()["detail"]["code"] == "legal_acceptance_required"

    accepted = _register(
        client, "legal_signup", acceptance=_acceptance("2026-09-01", "en")
    )
    assert accepted.status_code == 200, accepted.text
    assert client.get("/api/auth/me").json()["legal_acceptance_required"] is False

    rows = _rows()
    assert len(rows) == 2
    assert {row["source"] for row in rows} == {"registration"}
    assert {row["locale"] for row in rows} == {"en"}
    assert all(row["accepted_at"] for row in rows)


def test_contrato_manipulado_no_crea_usuario(client, tmp_data_dir):
    from app.auth.auth import get_user_by_username

    _write_legal(tmp_data_dir, _legal())
    tampered = _acceptance()
    tampered["documents"][0]["content_sha256"] = "f" * 64

    response = _register(client, "legal_tampered", acceptance=tampered)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legal_contract_mismatch"
    assert asyncio.run(get_user_by_username("legal_tampered")) is None
    assert _rows() == []


def test_actualizacion_de_version_bloquea_sesion_y_pat_hasta_reaceptar(
    client, tmp_data_dir
):
    _write_legal(tmp_data_dir, _legal("v1"))
    assert (
        _register(client, "legal_reaccept", acceptance=_acceptance("v1")).status_code
        == 200
    )
    pat_response = client.post("/api/auth/tokens", json={"name": "vscode"})
    assert pat_response.status_code == 200
    pat = pat_response.json()["token"]

    _write_legal(tmp_data_dir, _legal("v2"))
    assert client.get("/api/auth/me").json()["legal_acceptance_required"] is True

    blocked_cookie = client.get("/api/v2/agents")
    assert blocked_cookie.status_code == 428
    assert blocked_cookie.json()["detail"]["code"] == "legal_acceptance_required"

    client.cookies.clear()
    blocked_pat = client.get(
        "/api/v2/agents", headers={"Authorization": f"Bearer {pat}"}
    )
    assert blocked_pat.status_code == 428
    assert blocked_pat.json()["detail"]["accept_url"] == "/app/legal-acceptance"

    client.cookies.clear()
    from app.auth.auth import create_token

    client.cookies.set("ga_token", create_token("legal_reaccept"))
    stale = client.post("/api/auth/legal-acceptances", json=_acceptance("v1"))
    assert stale.status_code == 409

    current = client.post("/api/auth/legal-acceptances", json=_acceptance("v2"))
    assert current.status_code == 200
    assert current.json()["legal_acceptance_required"] is False
    assert client.get("/api/v2/agents").status_code == 200
    assert len(_rows()) == 4


def test_aceptacion_repetida_es_idempotente(client, tmp_data_dir):
    _write_legal(tmp_data_dir, _legal())
    assert (
        _register(client, "legal_repeat", acceptance=_acceptance()).status_code == 200
    )

    for _ in range(2):
        response = client.post("/api/auth/legal-acceptances", json=_acceptance())
        assert response.status_code == 200

    assert len(_rows()) == 2


def test_derechos_gdpr_siguen_disponibles_mientras_falta_reaceptar(
    client, tmp_data_dir
):
    _write_legal(tmp_data_dir, _legal("v1"))
    assert (
        _register(client, "legal_gdpr", acceptance=_acceptance("v1")).status_code == 200
    )
    _write_legal(tmp_data_dir, _legal("v2"))

    exported = client.get("/api/auth/me/export")

    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert client.get("/api/auth/me/deletion-status").status_code == 200


def test_admin_no_puede_activar_un_contrato_incompleto(admin_client, tmp_data_dir):
    _write_legal(tmp_data_dir, _legal(required=False))

    response = admin_client.put(
        "/api/settings/platform",
        json={
            "legal": {
                "required": True,
                "ready": False,
                "accept_url": "javascript:alert(1)",
                "documents": {},
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_legal_contract"
    assert "legal.accept_url no es válida" in response.json()["detail"]["errors"]


def test_invitado_y_administrador_bootstrap_estan_exentos(
    client, admin_client, tmp_data_dir
):
    _write_legal(tmp_data_dir, _legal())

    assert admin_client.get("/api/v2/agents").status_code == 200
    client.cookies.clear()
    assert client.post("/api/auth/guest").status_code == 200
    assert client.get("/api/v2/agents").status_code == 200
