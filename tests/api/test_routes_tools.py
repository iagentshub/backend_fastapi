"""Tests de tools: GET, POST, DELETE /api/tools, subida/descarga de binario."""

from __future__ import annotations

import asyncio

_TOOL_PAYLOAD = {
    "name": "Test Tool",
    "description": "Una tool de prueba.",
    "language": "python",
    "content": "print('hola')",
}

_CPP_TOOL_PAYLOAD = {
    "name": "Test CPP Tool",
    "description": "Una tool binaria de prueba.",
    "language": "cpp",
}


def test_list_tools_empty(admin_client):
    r = admin_client.get("/api/tools")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_save_private_tool(admin_client):
    r = admin_client.post("/api/tools/private", json=_TOOL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Tool"
    assert "id" in data
    assert data["language"] == "python"
    assert data["content"] == "print('hola')"
    assert "binary_b64" not in data


def test_save_public_tool(admin_client):
    r = admin_client.post("/api/tools/public", json=_TOOL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["scope"] == "public"
    assert data["labels"] == ["public", "community"]
    assert data["owner_id"]


def test_save_tool_requires_language(admin_client):
    payload = {k: v for k, v in _TOOL_PAYLOAD.items() if k != "language"}
    r = admin_client.post("/api/tools/private", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "language"


def test_save_tool_rejects_invalid_language(admin_client):
    r = admin_client.post(
        "/api/tools/private", json={**_TOOL_PAYLOAD, "language": "ruby"}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "language"


def test_save_tool_rejects_labels_outside_catalog(admin_client):
    r = admin_client.post(
        "/api/tools/private", json={**_TOOL_PAYLOAD, "labels": ["inventada"]}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "labels"


def test_save_tool_ignores_client_id(admin_client):
    """Un id fabricado por el cliente se ignora en el alta: lo genera el servidor."""
    r = admin_client.post(
        "/api/tools/private", json={**_TOOL_PAYLOAD, "id": "mi-tool"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] and data["id"] != "mi-tool"


def test_update_tool_keeps_existing_id(admin_client):
    created = admin_client.post("/api/tools/private", json=_TOOL_PAYLOAD).json()
    r = admin_client.post(
        "/api/tools/private",
        json={**_TOOL_PAYLOAD, "id": created["id"], "name": "Editada"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == created["id"]
    assert data["name"] == "Editada"


def test_save_cpp_tool_without_binary(admin_client):
    """Crear una tool cpp sin binario: 200, catalogada sin binario, content
    forzado a vacío (el contenido de una tool cpp vive solo en el binario)."""
    r = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["language"] == "cpp"
    assert data["content"] == ""
    assert data.get("binary_filename") is None
    assert data.get("binary_size") is None


def test_cpp_tool_ignores_submitted_content(admin_client):
    r = admin_client.post(
        "/api/tools/private", json={**_CPP_TOOL_PAYLOAD, "content": "int main(){}"}
    )
    assert r.status_code == 200
    assert r.json()["content"] == ""


def test_get_private_tool(admin_client):
    created = admin_client.post("/api/tools/private", json=_TOOL_PAYLOAD).json()
    r = admin_client.get(f"/api/tools/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_tool_not_found(admin_client):
    r = admin_client.get("/api/tools/private/nonexistent-tool")
    assert r.status_code == 404


def test_delete_private_tool(admin_client):
    created = admin_client.post("/api/tools/private", json=_TOOL_PAYLOAD).json()
    r = admin_client.delete(f"/api/tools/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_owned_public_tool(admin_client):
    created = admin_client.post("/api/tools/public", json=_TOOL_PAYLOAD).json()
    r = admin_client.delete(f"/api/tools/public/{created['id']}")
    assert r.status_code == 200


def test_other_user_cannot_edit_or_delete_public_tool(admin_client):
    from app.auth.auth import create_token, register_user

    created = admin_client.post("/api/tools/public", json=_TOOL_PAYLOAD).json()
    asyncio.run(register_user("toolother", "pass1234", email="toolother@example.com"))
    admin_client.cookies.set("ga_token", create_token("toolother"))

    edited = admin_client.post(
        "/api/tools/public",
        json={**_TOOL_PAYLOAD, "id": created["id"], "name": "Secuestrada"},
    )
    deleted = admin_client.delete(f"/api/tools/public/{created['id']}")
    assert edited.status_code == 403
    assert deleted.status_code == 403


def test_tools_requires_auth(client):
    r = client.get("/api/tools")
    assert r.status_code == 401


# ── binario ────────────────────────────────────────────────────────────────────


def test_upload_binary_arbitrary_extension(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={
            "file": ("prog.out", b"\x7fELF\x02\x01\x01", "application/octet-stream")
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["binary_filename"] == "prog.out"
    assert data["binary_size"] == 7
    assert "binary_b64" not in data


def test_upload_binary_without_extension(admin_client):
    """Un binario ELF en Linux normalmente no tiene extensión — debe aceptarse."""
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("progbin", b"\x7fELF", "application/octet-stream")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["binary_filename"] == "progbin"
    assert data["binary_size"] == 4


def test_get_tool_after_binary_upload_excludes_binary_b64(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"\x00\x01\x02", "application/octet-stream")},
    )
    r = admin_client.get(f"/api/tools/private/{created['id']}")
    assert r.status_code == 200
    data = r.json()
    assert "binary_b64" not in data
    assert data["binary_filename"] == "prog"
    assert data["binary_size"] == 3


def test_list_tools_excludes_binary_b64(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"\x00\x01\x02", "application/octet-stream")},
    )
    r = admin_client.get("/api/tools?scope=private")
    assert r.status_code == 200
    items = [it for it in r.json() if it["id"] == created["id"]]
    assert len(items) == 1
    assert "binary_b64" not in items[0]


def test_upload_binary_too_large(admin_client, monkeypatch):
    import app.api.routes.tools as tools_route

    # El límite real (50 MB) es imposible de ejercitar con un payload HTTP real
    # en un test rápido y además choca con BodySizeLimitMiddleware (límite
    # global de request, ~2 MB por defecto) antes de llegar a esta ruta —  se
    # baja el umbral en caliente para probar la validación en sí.
    monkeypatch.setattr(tools_route, "_MAX_TOOL_BINARY_BYTES", 8)
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"0123456789", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "tool_binary_too_large"


def test_upload_binary_empty_rejected(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "tool_binary_empty"


def test_upload_binary_on_non_cpp_tool_rejected(admin_client):
    created = admin_client.post("/api/tools/private", json=_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"data", "application/octet-stream")},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "tool_language_not_binary"


def test_download_binary_matches_uploaded_bytes(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    payload = b"\x00\x01binary-content\xff"
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog.bin", payload, "application/octet-stream")},
    )
    r = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert r.status_code == 200
    assert r.content == payload
    assert "prog.bin" in r.headers.get("content-disposition", "")


def test_download_binary_not_found(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert r.status_code == 404


def test_edit_name_after_binary_upload_preserves_binary(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    payload = b"persisted-binary"
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", payload, "application/octet-stream")},
    )
    r = admin_client.post(
        "/api/tools/private",
        json={**_CPP_TOOL_PAYLOAD, "id": created["id"], "name": "Nombre Editado"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Nombre Editado"

    download = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert download.status_code == 200
    assert download.content == payload
