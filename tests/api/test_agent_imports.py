import json

from app.api.routes import agent_imports as agent_import_routes
from app.services import agent_import_upload_sessions


def test_preview_requires_authentication(client) -> None:
    response = client.post(
        "/api/agents/import/preview",
        files={"file": ("agent.md", b"Prompt", "text/markdown")},
    )

    assert response.status_code == 401


def test_preview_does_not_persist_and_strips_client_identity(admin_client) -> None:
    before = admin_client.get("/api/v2/agents").json()["items"]
    response = admin_client.post(
        "/api/agents/import/preview",
        files={
            "file": (
                "agent.json",
                json.dumps(
                    {
                        "id": "chosen-by-client",
                        "owner_id": "another-user",
                        "scope": "public",
                        "name": "Imported draft",
                        "system_prompt": "Safe prompt",
                    }
                ).encode(),
                "application/json",
            )
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft"] == {
        "name": "Imported draft",
        "description": "",
        "agent_type": "generic",
        "model": "",
        "system_prompt": "Safe prompt",
        "temperature": 0.7,
        "scope": "private",
        "labels": ["private"],
    }
    assert set(body["ignored_fields"]) >= {"id", "owner_id", "scope"}
    assert admin_client.get("/api/v2/agents").json()["items"] == before


def test_preview_reports_invalid_extension_with_stable_error(admin_client) -> None:
    response = admin_client.post(
        "/api/agents/import/preview",
        files={"file": ("agent.exe", b"not an agent", "application/octet-stream")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_field"
    assert detail["field"] == "file"
    assert detail["reason"] == "unsupported_extension"


def test_preview_has_no_own_size_limit_by_default(admin_client) -> None:
    assert admin_client.get("/api/settings/platform").json()["max_request_bytes"] == 0
    prompt = b"a" * (2 * 1024 * 1024 + 1)

    response = admin_client.post(
        "/api/agents/import/preview",
        files={"file": ("large-agent.md", prompt, "text/markdown")},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["draft"]["system_prompt"]) == len(prompt)


def test_import_catalog_is_searched_and_paginated_per_resource_type(
    admin_client,
) -> None:
    for name in ("Security Review", "Security Operations", "Unrelated"):
        created = admin_client.post(
            "/api/skills/private", json={"name": name, "description": "d"}
        )
        assert created.status_code == 200, created.text

    first = admin_client.get(
        "/api/v2/agents/import/catalog/skill",
        params={"q": "Security", "limit": 1, "include_total": "true"},
    )

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["page"]["total"] == 2
    assert body["page"]["has_more"] is True
    assert body["page"]["next_cursor"]
    assert len(body["items"]) == 1
    assert "Security" in body["items"][0]["name"]

    second = admin_client.get(
        "/api/v2/agents/import/catalog/skill",
        params={
            "q": "Security",
            "limit": 1,
            "cursor": body["page"]["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["page"]["has_more"] is False
    assert second.json()["page"]["next_cursor"] is None

    wrong_query = admin_client.get(
        "/api/v2/agents/import/catalog/skill",
        params={
            "q": "Operations",
            "limit": 1,
            "cursor": body["page"]["next_cursor"],
        },
    )
    assert wrong_query.status_code == 422
    assert wrong_query.json()["detail"]["code"] == "invalid_cursor"

    obsolete = admin_client.get(
        "/api/v2/agents/import/catalog/skill", params={"offset": 1}
    )
    assert obsolete.status_code == 422
    assert obsolete.json()["detail"]["field"] == "offset"


def test_import_catalog_resolves_linked_ids_in_one_batched_request(
    admin_client,
) -> None:
    created = admin_client.post(
        "/api/skills/private",
        json={"name": "Linked Skill", "description": "d"},
    )
    assert created.status_code == 200, created.text
    skill_id = created.json()["id"]

    response = admin_client.post(
        "/api/agents/import/catalog/resolve",
        json={
            "resources": {
                "skill": [skill_id],
                "knowledge": ["missing-knowledge"],
            }
        },
    )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["skill"]] == [skill_id]
    assert response.json()["knowledge"] == []
    assert "prompt" not in response.json()


def test_preview_respects_admin_global_request_limit(admin_client) -> None:
    updated = admin_client.put(
        "/api/settings/platform", json={"max_request_bytes": 128}
    )
    assert updated.status_code == 200, updated.text

    response = admin_client.post(
        "/api/agents/import/preview",
        files={"file": ("agent.md", b"prompt", "text/markdown")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == {
        "code": "payload_too_large",
        "message": "Payload demasiado grande",
        "limit_bytes": 128,
    }


def _directory_files():
    return [
        (
            "files",
            (
                "x.md",
                b"---\nname: Agent X\nskills: [A, B]\n---\nUse A and B.",
                "text/markdown",
            ),
        ),
        (
            "files",
            (
                "y.md",
                b"---\nname: Agent Y\nskills: [Z, A]\n---\nUse Z and A.",
                "text/markdown",
            ),
        ),
        ("files", ("SKILL.md", b"---\nname: A\n---\nSkill A", "text/markdown")),
        ("files", ("SKILL.md", b"---\nname: B\n---\nSkill B", "text/markdown")),
        ("files", ("SKILL.md", b"---\nname: Z\n---\nSkill Z", "text/markdown")),
    ]


def _directory_paths() -> list[str]:
    return [
        "agents/x.md",
        "agents/y.md",
        "skills/a/SKILL.md",
        "skills/b/SKILL.md",
        "skills/z/SKILL.md",
    ]


def test_directory_preview_builds_multi_agent_shared_graph(admin_client) -> None:
    response = admin_client.post(
        "/api/agents/import/directory/preview",
        files=_directory_files(),
        data={"paths": json.dumps(_directory_paths())},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert all(
        "content_hash" not in item and "agent" not in item
        for item in body["components"]
    )
    agents = {
        item["name"]: item for item in body["components"] if item["kind"] == "agent"
    }
    assert set(agents) == {"Agent X", "Agent Y"}
    assert {
        reference["local_component_id"] for reference in agents["Agent X"]["references"]
    } == {"a", "b"}
    assert {
        reference["local_component_id"] for reference in agents["Agent Y"]["references"]
    } == {"a", "z"}


def test_directory_progressive_upload_builds_the_same_review_session(
    admin_client,
) -> None:
    paths = _directory_paths()
    files = _directory_files()
    created = admin_client.post(
        "/api/agents/import/directory/upload-sessions",
        json={"total_files": len(files)},
    )
    assert created.status_code == 200, created.text
    upload_session_id = created.json()["session_id"]

    for index, ((_, file_part), path) in enumerate(zip(files, paths, strict=True)):
        filename, content, mime_type = file_part
        uploaded = admin_client.post(
            f"/api/agents/import/directory/upload-sessions/"
            f"{upload_session_id}/files",
            files={"file": (filename, content, mime_type)},
            data={"file_index": index, "relative_path": path},
        )
        assert uploaded.status_code == 200, uploaded.text

    completed = admin_client.post(
        f"/api/agents/import/directory/upload-sessions/"
        f"{upload_session_id}/complete"
    )

    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["session_id"]
    assert {item["name"] for item in body["components"] if item["kind"] == "agent"} == {
        "Agent X",
        "Agent Y",
    }


def test_directory_progressive_upload_requires_every_file(admin_client) -> None:
    created = admin_client.post(
        "/api/agents/import/directory/upload-sessions",
        json={"total_files": 2},
    ).json()

    response = admin_client.post(
        f"/api/agents/import/directory/upload-sessions/"
        f"{created['session_id']}/complete"
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "incomplete_upload_session"


def test_directory_progressive_upload_rejects_secret_before_staging(
    admin_client,
) -> None:
    created = admin_client.post(
        "/api/agents/import/directory/upload-sessions",
        json={"total_files": 1},
    ).json()

    response = admin_client.post(
        f"/api/agents/import/directory/upload-sessions/"
        f"{created['session_id']}/files",
        files={"file": (".env", b"TOKEN=secret", "text/plain")},
        data={"file_index": 0, "relative_path": ".env"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "possible_secret"


def test_directory_progressive_upload_respects_admin_total_limit(
    admin_client, monkeypatch
) -> None:
    monkeypatch.setattr(
        agent_import_upload_sessions, "configured_max_bytes", lambda: 3
    )
    created = admin_client.post(
        "/api/agents/import/directory/upload-sessions",
        json={"total_files": 1},
    ).json()

    response = admin_client.post(
        f"/api/agents/import/directory/upload-sessions/"
        f"{created['session_id']}/files",
        files={"file": ("agent.md", b"abcd", "text/markdown")},
        data={"file_index": 0, "relative_path": "agent.md"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["limit_bytes"] == 3


def test_directory_preview_rejects_too_many_files_before_reading(
    admin_client, monkeypatch
) -> None:
    monkeypatch.setattr(agent_import_routes, "DIRECTORY_IMPORT_MAX_FILES", 1)

    response = admin_client.post(
        "/api/agents/import/directory/preview",
        files=[
            ("files", ("a.md", b"A", "text/markdown")),
            ("files", ("b.md", b"B", "text/markdown")),
        ],
        data={"paths": json.dumps(["agents/a.md", "agents/b.md"])},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["reason"] == "too_many_files"


def test_directory_apply_creates_shared_dependency_once_and_is_atomic(
    admin_client,
    monkeypatch,
) -> None:
    preview = admin_client.post(
        "/api/agents/import/directory/preview",
        files=_directory_files(),
        data={"paths": json.dumps(_directory_paths())},
    ).json()
    agent_ids = [
        item["component_id"]
        for item in preview["components"]
        if item["kind"] == "agent"
    ]
    monkeypatch.setattr(
        "app.services.agent_directory_import.detect_components",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("apply must reuse the reviewed plan")
        ),
    )

    response = admin_client.post(
        "/api/agents/import/directory/apply",
        data={
            "session_id": preview["session_id"],
            "options": json.dumps({"selected_agent_ids": agent_ids}),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent_count"] == 2
    assert body["resource_count"] == 3
    assert all(
        set(item) == {"component_id", "resource_id", "name", "action"}
        for item in body["agents"]
    )
    created = {item["component_id"]: item["resource_id"] for item in body["resources"]}
    assert set(created) == {"a", "b", "z"}
    agents = {
        item["name"]: item
        for item in admin_client.get("/api/v2/agents").json()["items"]
        if item["name"] in {"Agent X", "Agent Y"}
    }
    assert set(agents["Agent X"]["skills"]) == {created["a"], created["b"]}
    assert set(agents["Agent Y"]["skills"]) == {created["a"], created["z"]}

    reused = admin_client.post(
        "/api/agents/import/directory/apply",
        data={
            "session_id": preview["session_id"],
            "options": json.dumps({"selected_agent_ids": agent_ids}),
        },
    )
    assert reused.status_code == 422
    assert reused.json()["detail"]["reason"] == "expired_import_session"


def test_directory_importing_one_agent_only_materializes_its_dependency_closure(
    admin_client,
) -> None:
    preview = admin_client.post(
        "/api/agents/import/directory/preview",
        files=_directory_files(),
        data={"paths": json.dumps(_directory_paths())},
    ).json()
    x_id = next(
        item["component_id"]
        for item in preview["components"]
        if item["name"] == "Agent X"
    )

    response = admin_client.post(
        "/api/agents/import/directory/apply",
        files=_directory_files(),
        data={
            "paths": json.dumps(_directory_paths()),
            "options": json.dumps({"selected_agent_ids": [x_id]}),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent_count"] == 1
    assert body["resource_count"] == 2
    assert [item["name"] for item in body["agents"]] == ["Agent X"]
    assert {item["component_id"] for item in body["resources"]} == {"a", "b"}
