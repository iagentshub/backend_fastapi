import json


def test_preview_requires_authentication(client) -> None:
    response = client.post(
        "/api/agents/import/preview",
        files={"file": ("agent.md", b"Prompt", "text/markdown")},
    )

    assert response.status_code == 401


def test_preview_does_not_persist_and_strips_client_identity(admin_client) -> None:
    before = admin_client.get("/api/agents").json()
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
    assert admin_client.get("/api/agents").json() == before


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


def test_directory_apply_creates_shared_dependency_once_and_is_atomic(
    admin_client,
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

    response = admin_client.post(
        "/api/agents/import/directory/apply",
        files=_directory_files(),
        data={
            "paths": json.dumps(_directory_paths()),
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
        for item in admin_client.get("/api/agents").json()
        if item["name"] in {"Agent X", "Agent Y"}
    }
    assert set(agents["Agent X"]["skills"]) == {created["a"], created["b"]}
    assert set(agents["Agent Y"]["skills"]) == {created["a"], created["z"]}


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
