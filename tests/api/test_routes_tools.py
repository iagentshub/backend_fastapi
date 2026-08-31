"""Tests de tools: GET, POST, DELETE /api/tools, subida/descarga de binario."""

from __future__ import annotations

import asyncio
import hashlib

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
    "target_os": "linux",
    "target_arch": "x64",
}

_ELF_X64 = b"\x7fELF\x02\x01" + (b"\x00" * 12) + b"\x3e\x00"


def test_list_tools_empty(admin_client):
    r = admin_client.get("/api/v2/tools")
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


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
    r = admin_client.post("/api/tools/private", json={**_TOOL_PAYLOAD, "id": "mi-tool"})
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
    """El código fuente C++ puede catalogarse antes de compilar el artefacto."""
    r = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["language"] == "cpp"
    assert data["content"] == ""
    assert data.get("binary_filename") is None
    assert data.get("binary_size") is None


def test_cpp_tool_preserves_imported_source_content(admin_client):
    r = admin_client.post(
        "/api/tools/private", json={**_CPP_TOOL_PAYLOAD, "content": "int main(){}"}
    )
    assert r.status_code == 200
    assert r.json()["content"] == "int main(){}"


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


def test_user_tool_implementation_is_held_for_review(admin_client):
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user("toolauthor", "pass1234", email="author@example.com"))
    admin_client.cookies.set("ga_token", create_token("toolauthor"))

    response = admin_client.post(
        "/api/tools/private",
        json={**_TOOL_PAYLOAD, "labels": ["private", "review"]},
    )

    assert response.status_code == 200, response.text
    assert "review" in response.json()["labels"]

    edited = admin_client.post(
        "/api/tools/private",
        json={
            **_TOOL_PAYLOAD,
            "id": response.json()["id"],
            "name": "Renombrada",
            "labels": ["private"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert "review" in edited.json()["labels"]

    attempted_quarantine = admin_client.post(
        "/api/tools/private",
        json={**_TOOL_PAYLOAD, "labels": ["private", "quarantine"]},
    )
    assert attempted_quarantine.status_code == 200
    assert "quarantine" not in attempted_quarantine.json()["labels"]

    admin_client.cookies.set("ga_token", create_token("testadmin"))
    approved = admin_client.post(
        "/api/tools/private",
        json={
            **_TOOL_PAYLOAD,
            "id": response.json()["id"],
            "labels": ["private"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["owner_id"] == response.json()["owner_id"]
    assert "review" not in approved.json()["labels"]


def test_tools_requires_auth(client):
    r = client.get("/api/v2/tools")
    assert r.status_code == 401


# ── binario ────────────────────────────────────────────────────────────────────


def test_upload_binary_arbitrary_extension(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog.out", _ELF_X64, "application/octet-stream")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["binary_filename"] == "prog.out"
    assert data["binary_size"] == len(_ELF_X64)
    assert data["binary_sha256"] == hashlib.sha256(_ELF_X64).hexdigest()
    assert "review" in data["labels"]
    assert "binary_b64" not in data


def test_upload_binary_rolls_back_if_version_cannot_be_recorded(
    admin_client, monkeypatch
):
    from app.errors import APIError
    from app.storage.tool_storage import ToolStorage

    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()

    async def fail_version(*_args, **_kwargs):
        raise APIError(503, "internal_error", "No se pudo registrar la versión")

    monkeypatch.setattr(
        "app.api.routes.tools._versions.create",
        fail_version,
    )
    response = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )

    assert response.status_code == 503

    async def persisted_state():
        storage = ToolStorage()
        return (
            await storage.get("private", created["id"], owner_id=created["owner_id"]),
            await storage.get_binary("private", created["id"]),
        )

    tool, artifact = asyncio.run(persisted_state())
    assert tool is not None
    assert tool["binary_filename"] is None
    assert artifact is None


def test_restore_binary_rolls_back_if_history_write_fails(admin_client, monkeypatch):
    from app.errors import APIError

    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    first_binary = _ELF_X64 + b"first"
    second_binary = _ELF_X64 + b"second"
    first = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("first", first_binary, "application/octet-stream")},
    )
    assert first.status_code == 200
    second = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("second", second_binary, "application/octet-stream")},
    )
    assert second.status_code == 200

    async def fail_version(*_args, **_kwargs):
        raise APIError(503, "internal_error", "No se pudo registrar la versión")

    monkeypatch.setattr(
        # El historial vive en su propio módulo desde que resource_management.py
        # se partió en dos; el nombre viejo seguía existiendo allí sin usarse, y
        # parchearlo dejaba el test pasando por donde ya no pasa nada.
        "app.api.routes.resource_versions_history._versions.create",
        fail_version,
    )
    response = admin_client.post(
        f"/api/resources/tool/{created['id']}/versions/2/restore"
    )

    assert response.status_code == 503
    downloaded = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert downloaded.status_code == 200
    assert downloaded.content == second_binary
    assert (
        downloaded.headers["etag"] == f'"{hashlib.sha256(second_binary).hexdigest()}"'
    )


def test_upload_binary_without_extension(admin_client):
    """Un binario ELF en Linux normalmente no tiene extensión — debe aceptarse."""
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("progbin", _ELF_X64, "application/octet-stream")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["binary_filename"] == "progbin"
    assert data["binary_size"] == len(_ELF_X64)


def test_get_tool_after_binary_upload_excludes_binary_b64(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )
    r = admin_client.get(f"/api/tools/private/{created['id']}")
    assert r.status_code == 200
    data = r.json()
    assert "binary_b64" not in data
    assert data["binary_filename"] == "prog"
    assert data["binary_size"] == len(_ELF_X64)
    assert data["binary_sha256"] == hashlib.sha256(_ELF_X64).hexdigest()
    assert data["binary_uploaded_by"] == "testadmin"
    assert "review" in data["labels"]


def test_list_tools_excludes_binary_b64(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )
    r = admin_client.get("/api/v2/tools?scope=private")
    assert r.status_code == 200
    items = [it for it in r.json()["items"] if it["id"] == created["id"]]
    assert len(items) == 1
    assert "binary_b64" not in items[0]


def test_upload_binary_respects_admin_global_request_limit(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    updated = admin_client.put(
        "/api/settings/platform", json={"max_request_bytes": 128}
    )
    assert updated.status_code == 200, updated.text

    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"0123456789", "application/octet-stream")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "payload_too_large"
    assert r.json()["detail"]["limit_bytes"] == 128


def test_upload_binary_empty_rejected(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", b"", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "tool_binary_empty"


def test_upload_rejects_binary_for_a_different_declared_target(admin_client):
    created = admin_client.post(
        "/api/tools/private",
        json={**_CPP_TOOL_PAYLOAD, "target_arch": "arm64"},
    ).json()

    response = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["field"] == "binary"
    assert detail["declared"] == ["linux", "arm64"]
    assert detail["detected"] == ["linux", "x64"]


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
    payload = _ELF_X64 + b"binary-content"
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog.bin", payload, "application/octet-stream")},
    )
    r = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert r.status_code == 200
    assert r.content == payload
    assert "prog.bin" in r.headers.get("content-disposition", "")
    assert r.headers["etag"] == f'"{hashlib.sha256(payload).hexdigest()}"'


def test_download_binary_honours_matching_etag(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    upload = admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )
    etag = f'"{upload.json()["binary_sha256"]}"'

    response = admin_client.get(
        f"/api/tools/private/{created['id']}/binary",
        headers={"If-None-Match": etag},
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag


def test_binary_filename_is_safe_in_download_headers(admin_client):
    from app.api.routes.tools import (
        _binary_content_disposition,
        _safe_binary_filename,
    )

    filename = _safe_binary_filename('../café";\r\nX-Evil: yes.bin')
    disposition = _binary_content_disposition(filename)

    assert filename == "café____X-Evil: yes.bin"
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert 'filename="caf_____X-Evil__yes.bin"' in disposition
    assert "filename*=UTF-8''caf%C3%A9____X-Evil%3A%20yes.bin" in disposition


def test_download_binary_not_found(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    r = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert r.status_code == 404


def test_edit_name_after_binary_upload_preserves_binary(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    payload = _ELF_X64 + b"persisted-binary"
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


def test_tool_history_restores_the_versioned_binary(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    first_payload = _ELF_X64 + b"first"
    second_payload = _ELF_X64 + b"second"
    for payload in (first_payload, second_payload):
        response = admin_client.post(
            f"/api/tools/private/{created['id']}/binary",
            files={"file": ("prog", payload, "application/octet-stream")},
        )
        assert response.status_code == 200, response.text

    versions = admin_client.get(f"/api/resources/tool/{created['id']}/versions").json()
    binary_versions = [item for item in versions if item["reason"] == "binary_upload"]
    assert len(binary_versions) == 2

    restored = admin_client.post(
        f"/api/resources/tool/{created['id']}/versions/"
        f"{binary_versions[-1]['version']}/restore"
    )
    assert restored.status_code == 200, restored.text
    download = admin_client.get(f"/api/tools/private/{created['id']}/binary")
    assert download.status_code == 200
    assert download.content == first_payload


def test_changing_cpp_to_python_removes_binary_and_integrity_metadata(admin_client):
    created = admin_client.post("/api/tools/private", json=_CPP_TOOL_PAYLOAD).json()
    admin_client.post(
        f"/api/tools/private/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )

    edited = admin_client.post(
        "/api/tools/private",
        json={
            **_TOOL_PAYLOAD,
            "id": created["id"],
            "labels": ["private"],
        },
    )

    assert edited.status_code == 200, edited.text
    data = edited.json()
    assert data["language"] == "python"
    assert data.get("binary_filename") is None
    assert data.get("binary_size") is None
    assert data.get("binary_sha256") is None
    assert data.get("binary_uploaded_by") is None
    assert (
        admin_client.get(f"/api/tools/private/{created['id']}/binary").status_code
        == 404
    )


def test_public_binary_in_review_is_not_distributed_to_other_users(admin_client):
    from app.auth.auth import create_token, register_user

    created = admin_client.post("/api/tools/public", json=_CPP_TOOL_PAYLOAD).json()
    admin_client.post(
        f"/api/tools/public/{created['id']}/binary",
        files={"file": ("prog", _ELF_X64, "application/octet-stream")},
    )
    asyncio.run(register_user("binaryreader", "pass1234", email="reader@example.com"))
    admin_client.cookies.set("ga_token", create_token("binaryreader"))

    response = admin_client.get(f"/api/tools/public/{created['id']}/binary")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


def test_public_source_in_review_is_not_distributed_to_other_users(admin_client):
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user("sourceauthor", "pass1234", email="author2@example.com"))
    admin_client.cookies.set("ga_token", create_token("sourceauthor"))
    created = admin_client.post("/api/tools/public", json=_TOOL_PAYLOAD).json()
    assert "review" in created["labels"]

    asyncio.run(register_user("sourcereader", "pass1234", email="reader2@example.com"))
    admin_client.cookies.set("ga_token", create_token("sourcereader"))
    response = admin_client.get(f"/api/tools/public/{created['id']}")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


def test_public_source_in_review_remains_inspectable_by_its_owner(admin_client):
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user("sourceowner", "pass1234", email="owner2@example.com"))
    admin_client.cookies.set("ga_token", create_token("sourceowner"))
    created = admin_client.post("/api/tools/public", json=_TOOL_PAYLOAD).json()

    response = admin_client.get(f"/api/tools/public/{created['id']}")

    assert response.status_code == 200
    assert response.json()["content"] == _TOOL_PAYLOAD["content"]
