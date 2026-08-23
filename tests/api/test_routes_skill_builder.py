from __future__ import annotations

import json


def _events(text: str) -> list[dict]:
    return [
        json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")
    ]


def test_complete_skill_markdown_skips_provider(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "Skill import connection",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    markdown = """---
name: high-end-visual-design
description: Enseña a crear interfaces visuales de alta calidad.
---

# Directiva

Conserva estas instrucciones completas.
""" + ("Contenido extenso. " * 1_000)

    async def must_not_call_provider(*args, **kwargs):
        raise AssertionError("Una SKILL.md completa no debe llamar al proveedor")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "app.api.routes.skill_builder.stream_chat",
        must_not_call_provider,
    )

    response = admin_client.post(
        "/api/skill-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "expert",
            "messages": [{"role": "user", "content": markdown}],
        },
    )

    assert response.status_code == 200
    done = next(
        event for event in _events(response.text) if event["type"] == "builder_done"
    )
    assert done["status"] == "ready"
    assert done["draft"]["name"] == "high-end-visual-design"
    assert "Contenido extenso" in done["draft"]["content"]


def test_skill_builder_preserva_codigo_de_credencial_ilegible(
    admin_client, monkeypatch
):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM ilegible",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()

    async def unreadable(*args, **kwargs):
        yield (
            'data: {"type":"error","code":"credential_unreadable",'
            '"message":"Mensaje de fallback"}\n\n'
        )

    monkeypatch.setattr("app.api.routes.skill_builder.stream_chat", unreadable)

    response = admin_client.post(
        "/api/skill-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [{"role": "user", "content": "Ayúdame"}],
        },
    )

    error = next(event for event in _events(response.text) if event["type"] == "error")
    assert error["code"] == "credential_unreadable"
