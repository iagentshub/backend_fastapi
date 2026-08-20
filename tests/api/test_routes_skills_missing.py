"""Tests de skills — casos no cubiertos: guest, paginación, move_folder, ownership."""

from __future__ import annotations

import asyncio

from app.auth.auth import create_token
from app.storage.guest import create_guest_user

_PAYLOAD = {"name": "Skill Test", "description": "desc", "content": "contenido"}


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


# ── Guest — list skills ────────────────────────────────────────────────────────


def test_guest_list_skills_all(client):
    _guest_client(client)
    r = client.get("/api/skills?scope=all")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_guest_list_skills_private(client):
    _guest_client(client)
    client.post("/api/skills/private", json=_PAYLOAD)
    r = client.get("/api/skills?scope=private")
    assert r.status_code == 200
    assert any(s["name"] == "Skill Test" for s in r.json())


def test_guest_list_skills_public_only(client):
    _guest_client(client)
    r = client.get("/api/skills?scope=public")
    assert r.status_code == 200


def test_guest_sees_user_created_public_skills(client, admin_client):
    created = admin_client.post("/api/skills/public", json=_PAYLOAD)
    assert created.status_code == 200

    _guest_client(client)
    public = client.get("/api/skills?scope=public")
    assert public.status_code == 200
    assert created.json()["id"] in {skill["id"] for skill in public.json()}


def test_guest_list_skills_offset_limit(client):
    _guest_client(client)
    for i in range(3):
        client.post("/api/skills/private", json={**_PAYLOAD, "name": f"Skill{i}"})
    r = client.get("/api/skills?scope=private&limit=2&offset=1")
    assert r.status_code == 200
    assert len(r.json()) <= 2


# ── Scope inválido ─────────────────────────────────────────────────────────────


def test_list_skills_invalid_scope(admin_client):
    r = admin_client.get("/api/skills?scope=invalid")
    assert r.status_code == 400


def test_get_skill_invalid_scope(admin_client):
    r = admin_client.get("/api/skills/invalid/someid")
    assert r.status_code == 400


def test_save_skill_invalid_scope(admin_client):
    r = admin_client.post("/api/skills/invalid", json=_PAYLOAD)
    assert r.status_code == 400


# ── Guest — get private skill ──────────────────────────────────────────────────


def test_guest_get_private_skill(client):
    _guest_client(client)
    created = client.post("/api/skills/private", json=_PAYLOAD).json()
    r = client.get(f"/api/skills/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_guest_get_private_skill_not_found(client):
    _guest_client(client)
    r = client.get("/api/skills/private/noexiste")
    assert r.status_code == 404


# ── Guest — save private skill ─────────────────────────────────────────────────


def test_guest_save_private_skill(client):
    _guest_client(client)
    r = client.post("/api/skills/private", json=_PAYLOAD)
    assert r.status_code == 200
    assert r.json()["scope"] == "private"


def test_guest_private_skills_are_isolated_by_session(client):
    _guest_client(client)
    created = client.post("/api/skills/private", json=_PAYLOAD)
    assert created.status_code == 200

    _guest_client(client)
    private = client.get("/api/skills?scope=private")
    assert private.status_code == 200
    assert created.json()["id"] not in {skill["id"] for skill in private.json()}


def test_guest_private_skill_se_escribe_en_la_base_de_datos(client):
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
                    f"SELECT COUNT(*) FROM skills WHERE owner_id={PH}", (owner,)
                )
            )

    _, gid = _guest_client(client)
    assert asyncio.run(_filas(gid)) == 0
    created = client.post("/api/skills/private", json=_PAYLOAD)

    assert created.status_code == 200
    assert asyncio.run(_filas(gid)) == 1


def test_guest_save_public_skill_forbidden(client):
    _guest_client(client)
    r = client.post("/api/skills/public", json=_PAYLOAD)
    assert r.status_code == 403


# ── Guest — delete private skill ───────────────────────────────────────────────


def test_guest_delete_private_skill(client):
    _guest_client(client)
    created = client.post("/api/skills/private", json=_PAYLOAD).json()
    r = client.delete(f"/api/skills/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_guest_delete_private_skill_not_found(client):
    _guest_client(client)
    r = client.delete("/api/skills/private/noexiste")
    assert r.status_code == 404


def test_guest_delete_public_skill_forbidden(client):
    _guest_client(client)
    r = client.delete("/api/skills/public/algoid")
    assert r.status_code == 403


# ── Paginación registrado ──────────────────────────────────────────────────────


def test_list_skills_with_limit(admin_client):
    for i in range(3):
        admin_client.post("/api/skills/private", json={**_PAYLOAD, "name": f"Pag{i}"})
    r = admin_client.get("/api/skills?scope=private&limit=2")
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_list_skills_with_offset(admin_client):
    for i in range(4):
        admin_client.post("/api/skills/private", json={**_PAYLOAD, "name": f"Off{i}"})
    r_all = admin_client.get("/api/skills?scope=private").json()
    r_off = admin_client.get("/api/skills?scope=private&offset=1").json()
    assert len(r_off) == len(r_all) - 1
