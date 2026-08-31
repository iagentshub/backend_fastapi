"""Tests de tools — casos no cubiertos: scope inválido, 404, paginación,
auth y bloqueo explícito de invitados (Tool no está en el allowlist de
GuestSession — a diferencia de skills/prompts, ver tools.py)."""

from __future__ import annotations

_PAYLOAD = {
    "name": "Tool Test",
    "description": "desc",
    "language": "python",
    "content": "print(1)",
}


# ── Scope inválido ─────────────────────────────────────────────────────────────


def test_list_tools_invalid_scope(admin_client):
    r = admin_client.get("/api/v2/tools?scope=invalid")
    assert r.status_code == 400


def test_get_tool_invalid_scope(admin_client):
    r = admin_client.get("/api/tools/invalid/someid")
    assert r.status_code == 400


def test_save_tool_invalid_scope(admin_client):
    r = admin_client.post("/api/tools/invalid", json=_PAYLOAD)
    assert r.status_code == 400


def test_delete_tool_invalid_scope(admin_client):
    r = admin_client.delete("/api/tools/invalid/someid")
    assert r.status_code == 400


def test_upload_binary_invalid_scope(admin_client):
    r = admin_client.post(
        "/api/tools/invalid/someid/binary",
        files={"file": ("prog", b"data", "application/octet-stream")},
    )
    assert r.status_code == 400


def test_download_binary_invalid_scope(admin_client):
    r = admin_client.get("/api/tools/invalid/someid/binary")
    assert r.status_code == 400


# ── Not found ────────────────────────────────────────────────────────────────


def test_delete_nonexistent_tool(admin_client):
    r = admin_client.delete("/api/tools/private/nonexistent")
    assert r.status_code == 404


def test_upload_binary_nonexistent_tool(admin_client):
    r = admin_client.post(
        "/api/tools/private/nonexistent/binary",
        files={"file": ("prog", b"data", "application/octet-stream")},
    )
    assert r.status_code == 404


def test_download_binary_nonexistent_tool(admin_client):
    r = admin_client.get("/api/tools/private/nonexistent/binary")
    assert r.status_code == 404


# ── Paginación ───────────────────────────────────────────────────────────────


def test_list_tools_with_limit(admin_client):
    for i in range(3):
        admin_client.post("/api/tools/private", json={**_PAYLOAD, "name": f"Pag{i}"})
    r = admin_client.get("/api/v2/tools?scope=private&limit=2")
    assert r.status_code == 200
    assert len(r.json()["items"]) <= 2


def test_list_tools_with_cursor(admin_client):
    for i in range(4):
        admin_client.post("/api/tools/private", json={**_PAYLOAD, "name": f"Off{i}"})
    first = admin_client.get("/api/v2/tools?scope=private&limit=2")
    second = admin_client.get(
        "/api/v2/tools",
        params={
            "scope": "private",
            "limit": 2,
            "cursor": first.json()["page"]["next_cursor"],
        },
    )
    assert {item["id"] for item in first.json()["items"]}.isdisjoint(
        item["id"] for item in second.json()["items"]
    )


# ── Auth ─────────────────────────────────────────────────────────────────────


def test_list_tools_requires_auth(client):
    r = client.get("/api/v2/tools")
    assert r.status_code == 401


def test_get_tool_requires_auth(client):
    r = client.get("/api/tools/private/someid")
    assert r.status_code == 401


def test_save_tool_requires_auth(client):
    r = client.post("/api/tools/private", json=_PAYLOAD)
    assert r.status_code == 401


def test_delete_tool_requires_auth(client):
    r = client.delete("/api/tools/private/someid")
    assert r.status_code == 401


def test_download_binary_requires_auth(client):
    r = client.get("/api/tools/private/someid/binary")
    assert r.status_code == 401


# ── Invitados ────────────────────────────────────────────────────────────────
# Las tools estuvieron cerradas al invitado mientras su sesión era un dict en
# memoria: GuestSession no las contemplaba y escribir la rama habría sido
# duplicar el handler. Desde que el invitado es un usuario efímero usa el mismo
# almacenamiento que todos, y las tools son parte de su espacio personal.


def _guest_client(client):
    import asyncio

    from app.auth.auth import create_token
    from app.storage.guest import create_guest_user

    gid = asyncio.run(create_guest_user())
    client.cookies.set("ga_token", create_token(gid))
    return gid


def test_guest_lista_sus_tools(client):
    _guest_client(client)
    r = client.get("/api/v2/tools")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_guest_guarda_una_tool_privada(client):
    _guest_client(client)
    r = client.post("/api/tools/private", json=_PAYLOAD)
    assert r.status_code == 200, r.text
    assert any(t["id"] == r.json()["id"] for t in client.get("/api/v2/tools").json()["items"])


def test_guest_no_publica_una_tool(client):
    """Publicar sí sigue cerrado: lo que publicase se desvanece con su sesión."""
    _guest_client(client)
    r = client.post("/api/tools/public", json=_PAYLOAD)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "guest_cannot_publish"
