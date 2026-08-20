"""Tests de prompts — casos no cubiertos: guest, paginación, scope inválido, ownership."""

from __future__ import annotations

import asyncio

from app.auth.auth import create_token
from app.storage.guest import create_guest_user

_PAYLOAD = {
    "name": "Prompt Test",
    "description": "desc",
    "content": "contenido",
    "alias": "prompt-test",
}


# ── Helpers ────────────────────────────────────────────────────────────────────


def _guest_client(client):
    """Invitado con fila en la BD y cookie puesta.

    Antes bastaba con firmar un token para un id inventado: no había fila que
    crear. Desde que el invitado es un usuario efímero, un token sin fila es una
    credencial de alguien que no existe, y la respuesta correcta es 401.
    """
    gid = asyncio.run(create_guest_user())
    client.cookies.set("ga_token", create_token(gid))
    return client, gid


# ── Guest — list prompts ───────────────────────────────────────────────────────


def test_guest_list_prompts_all(client):
    _guest_client(client)
    r = client.get("/api/prompts?scope=all")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_guest_list_prompts_private(client):
    _guest_client(client)
    client.post("/api/prompts/private", json=_PAYLOAD)
    r = client.get("/api/prompts?scope=private")
    assert r.status_code == 200
    assert any(p["name"] == "Prompt Test" for p in r.json())


def test_guest_list_prompts_public_only(client):
    _guest_client(client)
    r = client.get("/api/prompts?scope=public")
    assert r.status_code == 200


def test_guest_sees_user_created_public_prompts(client, admin_client):
    created = admin_client.post("/api/prompts/public", json=_PAYLOAD)
    assert created.status_code == 200

    _guest_client(client)
    public = client.get("/api/prompts?scope=public")
    assert public.status_code == 200
    assert created.json()["id"] in {p["id"] for p in public.json()}


def test_guest_list_prompts_offset_limit(client):
    _guest_client(client)
    for i in range(3):
        client.post(
            "/api/prompts/private",
            json={**_PAYLOAD, "name": f"Prompt{i}", "alias": f"prompt-{i}"},
        )
    r = client.get("/api/prompts?scope=private&limit=2&offset=1")
    assert r.status_code == 200
    assert len(r.json()) <= 2


# ── Scope inválido ─────────────────────────────────────────────────────────────


def test_list_prompts_invalid_scope(admin_client):
    r = admin_client.get("/api/prompts?scope=invalid")
    assert r.status_code == 400


def test_get_prompt_invalid_scope(admin_client):
    r = admin_client.get("/api/prompts/invalid/someid")
    assert r.status_code == 400


def test_save_prompt_invalid_scope(admin_client):
    r = admin_client.post("/api/prompts/invalid", json=_PAYLOAD)
    assert r.status_code == 400


# ── Guest — get private prompt ─────────────────────────────────────────────────


def test_guest_get_private_prompt(client):
    _guest_client(client)
    created = client.post("/api/prompts/private", json=_PAYLOAD).json()
    r = client.get(f"/api/prompts/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_guest_get_private_prompt_not_found(client):
    _guest_client(client)
    r = client.get("/api/prompts/private/noexiste")
    assert r.status_code == 404


# ── Guest — save private prompt ────────────────────────────────────────────────


def test_guest_save_private_prompt(client):
    _guest_client(client)
    r = client.post("/api/prompts/private", json=_PAYLOAD)
    assert r.status_code == 200
    assert r.json()["scope"] == "private"


def test_guest_private_prompts_are_isolated_by_session(client):
    _guest_client(client)
    created = client.post("/api/prompts/private", json=_PAYLOAD)
    assert created.status_code == 200

    _guest_client(client)
    private = client.get("/api/prompts?scope=private")
    assert private.status_code == 200
    assert created.json()["id"] not in {p["id"] for p in private.json()}


def test_guest_private_prompt_se_escribe_en_la_base_de_datos(client):
    """Lo contrario de lo que este test comprobaba: antes el recurso del
    invitado vivía en un dict del proceso y desaparecía al cambiar de worker.
    Que llegue a la tabla es la corrección."""
    from app.storage.db import PH, open_db

    async def _filas(owner: str) -> int:
        # Del invitado, no de la tabla entera: contar el total haría depender el
        # test del orden en que pytest baraja los ficheros.
        async with open_db() as conn:
            return int(
                await conn.fetchval(
                    f"SELECT COUNT(*) FROM prompts WHERE owner_id={PH}", (owner,)
                )
            )

    _, gid = _guest_client(client)
    assert asyncio.run(_filas(gid)) == 0
    created = client.post("/api/prompts/private", json=_PAYLOAD)

    assert created.status_code == 200
    assert asyncio.run(_filas(gid)) == 1


def test_guest_save_public_prompt_forbidden(client):
    _guest_client(client)
    r = client.post("/api/prompts/public", json=_PAYLOAD)
    assert r.status_code == 403


# ── Guest — delete private prompt ──────────────────────────────────────────────


def test_guest_delete_private_prompt(client):
    _guest_client(client)
    created = client.post("/api/prompts/private", json=_PAYLOAD).json()
    r = client.delete(f"/api/prompts/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_guest_delete_private_prompt_not_found(client):
    _guest_client(client)
    r = client.delete("/api/prompts/private/noexiste")
    assert r.status_code == 404


def test_guest_delete_public_prompt_forbidden(client):
    _guest_client(client)
    r = client.delete("/api/prompts/public/algoid")
    assert r.status_code == 403


# ── Paginación registrado ───────────────────────────────────────────────────────


def test_list_prompts_with_limit(admin_client):
    for i in range(3):
        admin_client.post(
            "/api/prompts/private",
            json={**_PAYLOAD, "name": f"Pag{i}", "alias": f"pag-{i}"},
        )
    r = admin_client.get("/api/prompts?scope=private&limit=2")
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_list_prompts_with_offset(admin_client):
    for i in range(4):
        admin_client.post(
            "/api/prompts/private",
            json={**_PAYLOAD, "name": f"Off{i}", "alias": f"off-{i}"},
        )
    r_all = admin_client.get("/api/prompts?scope=private").json()
    r_off = admin_client.get("/api/prompts?scope=private&offset=1").json()
    assert len(r_off) == len(r_all) - 1
