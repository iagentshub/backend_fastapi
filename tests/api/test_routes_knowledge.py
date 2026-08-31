"""Tests de API para los recursos de conocimiento."""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import patch

import pytest

from app.auth.auth import create_token, register_user
from app.storage.knowledge import ExtractedDocument, KnowledgeStorage

# ── Helpers ────────────────────────────────────────────────────────────────────


def _setup_user(username: str) -> None:
    asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))


def _auth_client(client, username: str):
    client.cookies.set("ga_token", create_token(username))
    return client


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(client, patch_data_dir):
    _setup_user("alice")
    return _auth_client(client, "alice")


# ── Autenticación ──────────────────────────────────────────────────────────────


def test_list_items_requires_auth(client):
    r = client.get("/api/v2/knowledge")
    assert r.status_code == 401


def test_add_text_requires_auth(client):
    r = client.post("/api/knowledge/text", json={"title": "T", "content": "C"})
    assert r.status_code == 401


# ── Items: GET /api/knowledge ──────────────────────────────────────────────────


def test_list_items_empty(alice):
    r = alice.get("/api/v2/knowledge")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_list_items_after_add(alice):
    alice.post("/api/knowledge/text", json={"title": "Doc A", "content": "contenido A"})
    alice.post("/api/knowledge/text", json={"title": "Doc B", "content": "contenido B"})
    r = alice.get("/api/v2/knowledge")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_list_items_filter_by_type(alice):
    alice.post(
        "/api/knowledge/text", json={"title": "Texto", "content": "contenido de texto"}
    )
    # URL item via mock
    with patch(
        "app.api.routes.knowledge.items.fetch_url_document",
        return_value=ExtractedDocument(text="contenido url"),
    ):
        alice.post(
            "/api/knowledge/url", json={"url": "https://example.com", "title": "Web"}
        )

    r_text = alice.get("/api/v2/knowledge?type=text")
    assert r_text.status_code == 200
    items_text = r_text.json()["items"]
    assert all(i["type"] == "text" for i in items_text)
    assert len(items_text) == 1

    r_url = alice.get("/api/v2/knowledge?type=url")
    assert r_url.status_code == 200
    items_url = r_url.json()["items"]
    assert all(i["type"] == "url" for i in items_url)
    assert len(items_url) == 1


def test_list_items_pagination(alice):
    for i in range(5):
        alice.post(
            "/api/knowledge/text",
            json={"title": f"Doc {i}", "content": f"contenido {i}"},
        )

    r = alice.get("/api/v2/knowledge?limit=3")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 3

    r2 = alice.get(
        "/api/v2/knowledge",
        params={"limit": 3, "cursor": r.json()["page"]["next_cursor"]},
    )
    assert r2.status_code == 200
    assert len(r2.json()["items"]) == 2


def test_upload_directory_as_knowledge_pack(alice):
    response = alice.post(
        "/api/knowledge/packs",
        data={
            "name": "Scripts de despliegue",
            "description": "Runbooks y automatizaciones",
            "paths": '["scripts/deploy.py", "docs/README.md", "assets/design.zip", "assets/model.blend", ".env"]',
            "labels": '["private", "lang_es"]',
        },
        files=[
            ("files", ("deploy.py", b"print('deploy')", "text/x-python")),
            ("files", ("README.md", b"# Despliegue", "text/markdown")),
            ("files", ("design.zip", b"PK", "application/zip")),
            ("files", ("model.blend", b"BLENDER", "application/octet-stream")),
            ("files", (".env", b"TOKEN=secret", "text/plain")),
        ],
    )
    assert response.status_code == 200
    pack = response.json()
    assert pack["resource_type"] == "knowledge_pack"
    assert pack["name"] == "Scripts de despliegue"
    assert pack["file_count"] == 4
    assert [item["relative_path"] for item in pack["items"]] == [
        "assets/design.zip",
        "assets/model.blend",
        "docs/README.md",
        "scripts/deploy.py",
    ]
    assert pack["items"][0]["kind"] == "archive"
    assert pack["items"][1]["kind"] == "asset"
    assert pack["items"][3]["kind"] == "script"
    assert pack["ignored"] == [{"path": ".env", "reason": "posible_secreto"}]

    listed = alice.get("/api/v2/knowledge-packs")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["file_count"] == 4

    individual = alice.get("/api/v2/knowledge").json()["items"]
    assert {item["pack_id"] for item in individual} == {pack["id"]}
    assert {item["pack_relative_path"] for item in individual} == {
        "scripts/deploy.py",
        "docs/README.md",
        "assets/design.zip",
        "assets/model.blend",
    }
    deploy = next(
        item for item in individual if item["pack_relative_path"] == "scripts/deploy.py"
    )
    assert deploy["size_bytes"] == len(b"print('deploy')")
    assert deploy["mime_type"] == "text/x-python"
    assert deploy["checksum"] == hashlib.sha256(b"print('deploy')").hexdigest()


def test_pack_deactivate_keeps_it_visible_and_reactivates(alice):
    created = alice.post(
        "/api/knowledge/packs",
        data={"name": "Pack activable", "paths": '["manual.txt"]'},
        files=[("files", ("manual.txt", b"contenido", "text/plain"))],
    ).json()

    response = alice.post(f"/api/knowledge/packs/{created['id']}/deactivate")
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    listed = alice.get("/api/v2/knowledge-packs").json()["items"]
    assert (
        next(pack for pack in listed if pack["id"] == created["id"])["is_active"]
        is False
    )

    response = alice.post(f"/api/knowledge/packs/{created['id']}/activate")
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


def test_upload_pack_rejects_unsafe_relative_path(alice):
    response = alice.post(
        "/api/knowledge/packs",
        data={"name": "Unsafe", "paths": '["../secret.txt"]'},
        files=[("files", ("secret.txt", b"secret", "text/plain"))],
    )
    assert response.status_code == 422


def test_reference_pack_catalogues_metadata_without_copying_content(alice):
    response = alice.post(
        "/api/knowledge/packs",
        data={
            "name": "Referencias locales",
            "paths": '["photos/image.jpg"]',
            "sizes": "[1234]",
            "source_mode": "reference",
        },
        files=[("files", ("image.jpg", b"", "application/octet-stream"))],
    )
    assert response.status_code == 200
    pack = response.json()
    assert pack["source_mode"] == "reference"
    assert pack["last_synced_at"]
    assert pack["items"][0]["size_bytes"] == 1234
    item = asyncio.run(KnowledgeStorage().get(pack["items"][0]["id"]))
    assert item is not None
    assert "El contenido no se copió" in item["content"]


def test_resynchronize_pack_preserves_stable_ids_and_reports_changes(alice):
    pack = alice.post(
        "/api/knowledge/packs",
        data={
            "name": "Código sincronizado",
            "paths": '["src/a.py", "src/b.py"]',
            "source_mode": "upload",
        },
        files=[
            ("files", ("a.py", b"print('a')", "text/x-python")),
            ("files", ("b.py", b"print('b')", "text/x-python")),
        ],
    ).json()
    original_ids = {item["relative_path"]: item["id"] for item in pack["items"]}

    response = alice.post(
        f"/api/knowledge/packs/{pack['id']}/sync",
        data={"paths": '["src/b.py", "src/c.py"]'},
        files=[
            ("files", ("b.py", b"print('b2')", "text/x-python")),
            ("files", ("c.py", b"print('c')", "text/x-python")),
        ],
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["sync"] == {"added": 1, "updated": 1, "removed": 1, "total": 2}
    updated_ids = {item["relative_path"]: item["id"] for item in updated["items"]}
    assert updated_ids["src/b.py"] == original_ids["src/b.py"]
    assert "src/a.py" not in updated_ids
    assert "src/c.py" in updated_ids
    b_item = asyncio.run(KnowledgeStorage().get(updated_ids["src/b.py"]))
    assert b_item is not None
    assert "b2" in b_item["content"]


def test_uploaded_content_can_always_be_resynchronized(alice):
    pack = alice.post(
        "/api/knowledge/packs",
        data={"name": "Copia", "paths": '["file.md"]'},
        files=[("files", ("file.md", b"content", "text/markdown"))],
    ).json()
    response = alice.post(
        f"/api/knowledge/packs/{pack['id']}/sync",
        data={"paths": '["file.md"]'},
        files=[("files", ("file.md", b"new", "text/markdown"))],
    )
    assert response.status_code == 200
    assert response.json()["sync"]["updated"] == 1


def test_device_manifest_only_uploads_changed_files(alice):
    original_b = b"print('b')"
    pack = alice.post(
        "/api/knowledge/packs",
        data={"name": "Diferencial", "paths": '["src/a.py", "src/b.py"]'},
        files=[
            ("files", ("a.py", b"print('a')", "text/x-python")),
            ("files", ("b.py", original_b, "text/x-python")),
        ],
    ).json()
    original_ids = {item["relative_path"]: item["id"] for item in pack["items"]}
    new_c = b"print('c')"
    manifest = [
        {
            "relative_path": "src/b.py",
            "size_bytes": len(original_b),
            "checksum": hashlib.sha256(original_b).hexdigest(),
            "mime_type": "text/x-python",
            "modified_at": 123,
        },
        {
            "relative_path": "src/c.py",
            "size_bytes": len(new_c),
            "checksum": hashlib.sha256(new_c).hexdigest(),
            "mime_type": "text/x-python",
            "modified_at": 456,
        },
    ]
    comparison = alice.post(
        f"/api/knowledge/packs/{pack['id']}/sync-manifest",
        json={"files": manifest},
    )
    assert comparison.status_code == 200
    assert comparison.json() == {
        "upload_paths": ["src/c.py"],
        "unchanged": 1,
        "metadata_only": 0,
        "removed": 1,
        "total": 2,
    }

    response = alice.post(
        f"/api/knowledge/packs/{pack['id']}/sync",
        data={"manifest": json.dumps(manifest), "paths": '["src/c.py"]'},
        files=[("files", ("c.py", new_c, "text/x-python"))],
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["sync"] == {"added": 1, "updated": 0, "removed": 1, "total": 2}
    updated_ids = {item["relative_path"]: item["id"] for item in updated["items"]}
    assert updated_ids["src/b.py"] == original_ids["src/b.py"]
    assert "src/a.py" not in updated_ids


def test_device_manifest_rejects_content_that_does_not_match_checksum(alice):
    pack = alice.post(
        "/api/knowledge/packs",
        data={"name": "Verificado", "paths": '["file.md"]'},
        files=[("files", ("file.md", b"old", "text/markdown"))],
    ).json()
    manifest = [
        {
            "relative_path": "file.md",
            "size_bytes": 3,
            "checksum": hashlib.sha256(b"new").hexdigest(),
            "mime_type": "text/markdown",
        }
    ]
    response = alice.post(
        f"/api/knowledge/packs/{pack['id']}/sync",
        data={"manifest": json.dumps(manifest), "paths": '["file.md"]'},
        files=[("files", ("file.md", b"bad", "text/markdown"))],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "checksum"


def test_reference_manifest_uses_device_checksum_without_uploading_content(alice):
    raw = b"local-only"
    checksum = hashlib.sha256(raw).hexdigest()
    session = alice.post(
        "/api/knowledge/packs/upload-sessions",
        json={
            "name": "Referencia",
            "source_mode": "reference",
            "total_files": 1,
        },
    ).json()
    uploaded = alice.post(
        f"/api/knowledge/packs/upload-sessions/{session['id']}/files",
        data={
            "relative_path": "local.bin",
            "reported_size": len(raw),
            "reported_checksum": checksum,
            "reported_mime_type": "application/octet-stream",
        },
        files={"file": ("local.bin", b"", "application/octet-stream")},
    )
    assert uploaded.status_code == 200
    completed = alice.post(
        f"/api/knowledge/packs/upload-sessions/{session['id']}/complete"
    ).json()
    assert completed["items"][0]["checksum"] == checksum


def test_pack_upload_session_continues_after_failure_and_retries_file(alice):
    session_response = alice.post(
        "/api/knowledge/packs/upload-sessions",
        json={
            "name": "Carga grande",
            "source_mode": "upload",
            "labels": ["private", "lang_es"],
            "total_files": 3,
        },
    )
    assert session_response.status_code == 200
    session = session_response.json()
    session_id = session["id"]
    assert session["upload_status"] == "uploading"
    assert alice.get("/api/v2/knowledge-packs").json()["items"] == []

    for path in ("docs/a.md", "docs/c.md"):
        uploaded = alice.post(
            f"/api/knowledge/packs/upload-sessions/{session_id}/files",
            data={"relative_path": path, "reported_size": 4},
            files={"file": (path.rsplit("/", 1)[-1], b"test", "text/markdown")},
        )
        assert uploaded.status_code == 200

    # El techo por archivo lo pone ahora `max_request_bytes`, no esta ruta: lo
    # que sigue rechazando aquí es el acumulado de la sesión, que se reparte en
    # varias peticiones y por eso ningún middleware puede contarlo. Se baja en
    # caliente porque llegar al real exige subir medio giga.
    import app.api.routes.knowledge.pack_sessions as sessions_route

    with pytest.MonkeyPatch.context() as techo:
        techo.setattr(sessions_route, "_PACK_SESSION_MAX_TOTAL_BYTES", 8)
        failed = alice.post(
            f"/api/knowledge/packs/upload-sessions/{session_id}/files",
            data={"relative_path": "docs/b.md", "reported_size": 9},
            files={"file": ("b.md", b"x" * 9, "text/markdown")},
        )
    assert failed.status_code == 413

    retried = alice.post(
        f"/api/knowledge/packs/upload-sessions/{session_id}/files",
        data={"relative_path": "docs/b.md", "reported_size": 5},
        files={"file": ("b.md", b"retry", "text/markdown")},
    )
    assert retried.status_code == 200

    completed = alice.post(
        f"/api/knowledge/packs/upload-sessions/{session_id}/complete"
    )
    assert completed.status_code == 200
    assert completed.json()["upload_status"] == "ready"
    assert completed.json()["file_count"] == 3
    assert len(alice.get("/api/v2/knowledge-packs").json()["items"]) == 1


def test_delete_pack_removes_its_catalogued_items(alice):
    created = alice.post(
        "/api/knowledge/packs",
        data={"name": "Temporal", "paths": '["notes/a.md"]'},
        files=[("files", ("a.md", b"contenido", "text/markdown"))],
    ).json()
    assert alice.delete(f"/api/knowledge/packs/{created['id']}").status_code == 200
    assert alice.get("/api/v2/knowledge-packs").json()["items"] == []
    assert alice.get("/api/v2/knowledge").json()["items"] == []


def test_publish_pack_exposes_pack_files_and_graph_as_one_unit(alice):
    created = alice.post(
        "/api/knowledge/packs",
        data={
            "name": "Pack público",
            "paths": '["skills/SKILL.md", "scripts/run.sh"]',
            "labels": '["public", "production", "lang_es"]',
        },
        files=[
            ("files", ("SKILL.md", b"# Skill\nInstructions", "text/markdown")),
            ("files", ("run.sh", b"echo ok", "text/x-shellscript")),
        ],
    ).json()
    assert created["scope"] == "public"
    assert "public" in created["labels"]

    _setup_user("bob_user")
    _auth_client(alice, "bob_user")
    grouped = alice.get("/api/v2/explore?pack_mode=true").json()["items"]
    assert [item["resource_type"] for item in grouped] == ["knowledge_pack"]
    individual = alice.get("/api/v2/explore?pack_mode=false").json()["items"]
    assert {item["resource_type"] for item in individual} == {"knowledge"}
    assert {item["pack_id"] for item in individual} == {created["id"]}

    relations = alice.get(f"/api/explore/knowledge_pack/{created['id']}/relations")
    assert relations.status_code == 200
    payload = relations.json()
    assert payload["root"]["id"] == created["id"]
    # Las carpetas no viajan: el backend manda la ruta de cada fichero y el
    # árbol lo construye el cliente.
    carpetas = {
        item["path"].rsplit("/", 1)[0]
        for item in payload["items"]
        if "/" in item.get("path", "")
    }
    assert carpetas == {"skills", "scripts"}
    assert len(payload["items"]) == 2


def test_group_owned_pack_can_be_published_as_one_unit(alice):
    group = alice.post("/api/groups", json={"name": "Knowledge Team"}).json()
    alice.cookies.set("ga_token", create_token("alice", group_id=group["id"]))
    pack = alice.post(
        "/api/knowledge/packs",
        data={"name": "Pack del grupo", "paths": '["docs/team.md"]'},
        files=[("files", ("team.md", b"# Team", "text/markdown"))],
    ).json()
    assert pack["owner_id"] == group["id"]

    published = alice.put(
        f"/api/knowledge-packs/{pack['id']}/visibility",
        json={"is_public": True, "category": "Other"},
    )
    assert published.status_code == 200
    relations = alice.get(f"/api/explore/knowledge_pack/{pack['id']}/relations")
    assert relations.status_code == 200

    unpublished = alice.put(
        f"/api/knowledge-packs/{pack['id']}/visibility",
        json={"is_public": False, "category": "Other"},
    )
    assert unpublished.status_code == 200
    assert (
        alice.get(f"/api/explore/knowledge_pack/{pack['id']}/relations").status_code
        == 404
    )


# ── Items: POST /api/knowledge/text ───────────────────────────────────────────


def test_add_text_item(alice):
    r = alice.post(
        "/api/knowledge/text",
        json={"title": "Mi nota", "content": "Este es el contenido de prueba."},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Mi nota"
    assert data["type"] == "text"
    assert data["char_count"] == len("Este es el contenido de prueba.")
    assert "id" in data


def test_add_text_item_missing_title(alice):
    r = alice.post("/api/knowledge/text", json={"title": "", "content": "Algo"})
    assert r.status_code == 422


def test_add_text_item_missing_content(alice):
    r = alice.post("/api/knowledge/text", json={"title": "Título", "content": ""})
    assert r.status_code == 422


# ── Items: POST /api/knowledge/url ────────────────────────────────────────────


def test_add_url_item_mocked(alice):
    with patch(
        "app.api.routes.knowledge.items.fetch_url_document",
        return_value=ExtractedDocument(text="texto extraído de la web"),
    ):
        r = alice.post(
            "/api/knowledge/url",
            json={"url": "https://example.com", "title": "Ejemplo"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "url"
    assert data["title"] == "Ejemplo"
    assert data["source"] == "https://example.com"


def test_add_url_item_missing_url(alice):
    r = alice.post("/api/knowledge/url", json={"url": ""})
    assert r.status_code == 422


def test_add_url_item_fetch_error(alice):
    with patch(
        "app.api.routes.knowledge.items.fetch_url_document",
        side_effect=Exception("timeout"),
    ):
        r = alice.post("/api/knowledge/url", json={"url": "https://bad.example.com"})
    assert r.status_code == 422


# ── Items: DELETE /api/knowledge/{id} ─────────────────────────────────────────


def test_delete_item(alice):
    item = alice.post(
        "/api/knowledge/text", json={"title": "Borrar", "content": "contenido"}
    ).json()
    r = alice.delete(f"/api/knowledge/{item['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Ya no aparece en la lista
    items = alice.get("/api/v2/knowledge").json()["items"]
    assert all(i["id"] != item["id"] for i in items)


def test_delete_item_not_found(alice):
    r = alice.delete("/api/knowledge/nonexistent-id")
    assert r.status_code == 404


# ── Documento: POST /api/knowledge/document ───────────────────────────────────


def test_upload_txt_document(alice):
    content = b"Este es un documento de texto plano."
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("nota.txt", content, "text/plain")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "document"
    assert data["title"] == "nota.txt"


def test_upload_document_preserves_content_language_labels(alice):
    response = alice.post(
        "/api/knowledge/document",
        files={"file": ("manual.txt", b"Manual en espanol", "text/plain")},
        data={"labels": '["private", "lang_es", "lang_en"]'},
    )
    assert response.status_code == 200
    assert response.json()["labels"] == [
        "private",
        "community",
        "lang_es",
        "lang_en",
    ]


def test_upload_image_document_uses_ocr(alice, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.knowledge._shared.extract_document",
        lambda content, filename, mime: ExtractedDocument(
            text="Texto reconocido en la imagen"
        ),
    )
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("imagen.png", b"\x89PNG", "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "Texto reconocido en la imagen"
    assert r.json()["mime_type"] == "image/png"
    assert r.json()["size_bytes"] == 4


def test_knowledge_active_toggle_and_labels_are_editable(alice):
    item = alice.post(
        "/api/knowledge/text",
        json={"title": "Guía", "content": "Contenido"},
    ).json()
    deactivated = alice.post(f"/api/knowledge/{item['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    updated = alice.put(
        f"/api/knowledge/{item['id']}/labels",
        json={"labels": ["private", "community", "development", "lang_es", "lang_en"]},
    )
    assert updated.status_code == 200
    assert updated.json()["labels"] == [
        "private",
        "community",
        "development",
        "lang_es",
        "lang_en",
    ]

    published = alice.put(
        f"/api/knowledge/{item['id']}/labels",
        json={"labels": ["public", "production", "lang_es"]},
    )
    assert published.status_code == 200
    assert published.json()["labels"] == [
        "public",
        "community",
        "production",
        "lang_es",
    ]
    assert published.json()["is_active"] is False

    activated = alice.post(f"/api/knowledge/{item['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    _setup_user("knowledge_reader")
    _auth_client(alice, "knowledge_reader")
    assert any(
        row["resource_type"] == "knowledge" and row["resource_id"] == item["id"]
        for row in alice.get("/api/v2/explore").json()["items"]
    )

    _auth_client(alice, "alice")
    private = alice.put(
        f"/api/knowledge/{item['id']}/labels",
        json={"labels": ["private", "development", "lang_es"]},
    )
    assert private.status_code == 200
    _auth_client(alice, "knowledge_reader")
    assert all(
        row["resource_type"] != "knowledge" or row["resource_id"] != item["id"]
        for row in alice.get("/api/v2/explore").json()["items"]
    )


def test_pack_label_edit_applies_to_all_members(alice):
    pack = alice.post(
        "/api/knowledge/packs",
        data={"name": "Pack", "paths": '["guide.md"]'},
        files=[("files", ("guide.md", b"Guide", "text/markdown"))],
    ).json()
    updated = alice.put(
        f"/api/knowledge/packs/{pack['id']}/labels",
        json={"labels": ["private", "community", "production", "lang_es"]},
    )
    assert updated.status_code == 200
    assert updated.json()["labels"] == [
        "private",
        "community",
        "production",
        "lang_es",
    ]
    items = alice.get("/api/v2/knowledge").json()["items"]
    assert items[0]["labels"] == updated.json()["labels"]


def test_edit_item_updates_name_and_labels_without_rewriting_file_metadata(alice):
    created = alice.post(
        "/api/knowledge/document",
        files={"file": ("original.md", b"Original content", "text/markdown")},
    ).json()

    updated = alice.put(
        f"/api/knowledge/{created['id']}",
        json={
            "name": "Manual renombrado",
            "labels": ["private", "production", "lang_es"],
        },
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["name"] == "Manual renombrado"
    assert payload["title"] == "Manual renombrado"
    assert payload["source"] == created["source"]
    assert payload["content"] == created["content"]
    assert payload["labels"] == ["private", "community", "production", "lang_es"]


def test_edit_pack_updates_metadata_and_propagates_labels(alice):
    pack = alice.post(
        "/api/knowledge/packs",
        data={"name": "Pack", "paths": '["guide.md"]'},
        files=[("files", ("guide.md", b"Guide", "text/markdown"))],
    ).json()

    updated = alice.put(
        f"/api/knowledge/packs/{pack['id']}",
        json={
            "name": "Pack de producción",
            "description": "Documentación operativa",
            "labels": ["private", "production", "lang_es", "lang_en"],
        },
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["name"] == "Pack de producción"
    assert payload["description"] == "Documentación operativa"
    assert payload["labels"] == [
        "private",
        "community",
        "production",
        "lang_es",
        "lang_en",
    ]
    items = alice.get("/api/v2/knowledge").json()["items"]
    assert items[0]["labels"] == payload["labels"]


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/knowledge/{id}", {"name": "  "}),
        ("/api/knowledge/packs/{id}", {"name": "  "}),
    ],
)
def test_edit_knowledge_rejects_empty_name(alice, path, payload):
    if "/packs/" in path:
        resource = alice.post(
            "/api/knowledge/packs",
            data={"name": "Pack", "paths": '["guide.md"]'},
            files=[("files", ("guide.md", b"Guide", "text/markdown"))],
        ).json()
    else:
        resource = alice.post(
            "/api/knowledge/text", json={"title": "Nota", "content": "Contenido"}
        ).json()
    response = alice.put(path.format(id=resource["id"]), json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "name_required"


def test_upload_binary_document_is_catalogued(alice):
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("datos.zip", b"PK", "application/zip")},
    )
    assert r.status_code == 200
    assert "Archivo catalogado" in r.json()["content"]


def test_upload_secret_document_is_rejected(alice):
    r = alice.post(
        "/api/knowledge/document",
        files={"file": (".env", b"TOKEN=secret", "text/plain")},
    )
    assert r.status_code == 422


def test_upload_document_requires_auth(client):
    r = client.post(
        "/api/knowledge/document",
        files={"file": ("nota.txt", b"contenido", "text/plain")},
    )
    assert r.status_code == 401


def test_documento_recortado_lo_dice_en_la_ficha(alice, monkeypatch):
    """El aviso tiene que llegar al cliente, no quedarse en el extractor.

    Sin esto la ficha guarda el texto a medias con un `char_count` que es el del
    recorte, y no hay nada —ni columna, ni campo, ni log— que distinga eso de un
    documento que entró entero. El original no se conserva: si el aviso se
    pierde aquí, se pierde para siempre.
    """
    monkeypatch.setattr(
        "app.api.routes.knowledge._shared.extract_document",
        lambda content, filename, mime: ExtractedDocument(
            text="lo que cupo",
            truncated=True,
            source_chars=1_500_000,
            reason="max_chars",
        ),
    )
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("enorme.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 200
    ficha = r.json()
    assert ficha["content_truncated"] is True
    assert ficha["truncation_reason"] == "max_chars"
    assert ficha["source_char_count"] == 1_500_000
    assert ficha["char_count"] == len("lo que cupo")

    listado = alice.get("/api/v2/knowledge?type=document").json()["items"]
    fila = next(item for item in listado if item["id"] == ficha["id"])
    assert fila["content_truncated"] is True
    assert fila["source_char_count"] == 1_500_000


def test_documento_completo_no_se_marca(alice, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.knowledge._shared.extract_document",
        lambda content, filename, mime: ExtractedDocument(
            text="texto entero", source_chars=len("texto entero")
        ),
    )
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("normal.pdf", b"%PDF-1.4", "application/pdf")},
    )
    ficha = r.json()
    assert ficha["content_truncated"] is False
    assert ficha["source_char_count"] == ficha["char_count"]


def test_un_documento_grande_ya_no_lo_rechaza_esta_ruta(alice):
    """El techo de subida lo pone `max_request_bytes`, no un literal aquí.

    Había un segundo límite de 10 MB escrito a mano y por debajo del panel: con
    el administrador en «sin límite», la interfaz dejaba elegir el fichero y era
    esa línea la que lo rechazaba, con un número que no aparece en ninguna
    pantalla. Ver docs/adr/011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md
    """
    grande = b"contenido de prueba\n" * 600_000  # ~11,4 MB
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("grande.txt", grande, "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["size_bytes"] == len(grande)
