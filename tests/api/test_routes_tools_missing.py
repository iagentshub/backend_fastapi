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
    r = admin_client.get("/api/tools?scope=invalid")
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


def test_activate_nonexistent_tool(admin_client):
    r = admin_client.post("/api/tools/nonexistent/activate")
    assert r.status_code == 404


def test_deactivate_nonexistent_tool(admin_client):
    r = admin_client.post("/api/tools/nonexistent/deactivate")
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
    r = admin_client.get("/api/tools?scope=private&limit=2")
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_list_tools_with_offset(admin_client):
    for i in range(4):
        admin_client.post("/api/tools/private", json={**_PAYLOAD, "name": f"Off{i}"})
    r_all = admin_client.get("/api/tools?scope=private").json()
    r_off = admin_client.get("/api/tools?scope=private&offset=1").json()
    assert len(r_off) == len(r_all) - 1


# ── Auth ─────────────────────────────────────────────────────────────────────


def test_list_tools_requires_auth(client):
    r = client.get("/api/tools")
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
# GuestSession no contempla tools (ver docstring de tools.py): a diferencia de
# skills/prompts, subir binarios ejecutables desde una sesión de demo efímera
# no aporta nada al producto — las rutas exigen cuenta registrada.


def test_guest_cannot_list_tools(client):
    from app.auth.auth import create_token
    from app.storage.guest import new_guest_id

    gid = new_guest_id()
    client.cookies.set("ga_token", create_token(gid))
    r = client.get("/api/tools")
    assert r.status_code == 403


def test_guest_cannot_save_tool(client):
    from app.auth.auth import create_token
    from app.storage.guest import new_guest_id

    gid = new_guest_id()
    client.cookies.set("ga_token", create_token(gid))
    r = client.post("/api/tools/private", json=_PAYLOAD)
    assert r.status_code == 403
