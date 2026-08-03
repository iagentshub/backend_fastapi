from __future__ import annotations

import json


def _events(response_text: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


def test_builder_requires_auth(client):
    response = client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": "missing",
            "messages": [{"role": "user", "content": "Crea un agente"}],
        },
    )

    assert response.status_code == 401


def test_builder_rejects_unavailable_connection(admin_client):
    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": "missing",
            "messages": [{"role": "user", "content": "Crea un agente"}],
        },
    )

    assert response.status_code == 404


def test_builder_returns_validated_draft(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM test",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()

    captured = {}

    async def fake_stream_chat(agent, *args, **kwargs):
        captured["timeout"] = agent.timeout
        captured["system_prompt"] = agent.system_prompt
        captured["agent_model"] = agent.model
        captured["connection_model"] = args[0]["model"]
        reply = json.dumps(
            {
                "assistant_message": "He preparado el borrador.",
                "status": "ready",
                "draft": {
                    "name": "Agente de pruebas",
                    "description": "Creado mediante conversación",
                    "system_prompt": (
                        "Eres un agente de pruebas. Verifica cada resultado y explica "
                        "claramente cualquier limitación."
                    ),
                    "temperature": 0.3,
                    "skills": [],
                    "knowledge": [],
                    "use_memory": False,
                },
            },
            ensure_ascii=False,
        )
        yield f"data: {json.dumps({'type': 'token', 'token': '{'})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr(
        "app.api.routes.agent_builder.stream_chat",
        fake_stream_chat,
    )

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "messages": [
                {
                    "role": "user",
                    "content": "Crea un agente que compruebe resultados de pruebas",
                }
            ],
            "resources": {"skills": [], "knowledge": []},
        },
    )

    assert response.status_code == 200
    events = _events(response.text)
    assert any(event["type"] == "progress" for event in events)
    done = next(event for event in events if event["type"] == "builder_done")
    assert done["status"] == "ready"
    assert done["draft"]["name"] == "Agente de pruebas"
    assert captured["timeout"] == 90
    assert captured["agent_model"] == "meta/llama-3.2-3b-instruct"
    assert captured["connection_model"] == "meta/llama-3.2-3b-instruct"


def test_expert_mode_never_returns_another_question(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM expert",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    specification = (
        "Eres un agente senior especializado en Python y FastAPI. "
        "Revisa seguridad, rendimiento, pruebas y compatibilidad. "
    )

    async def fake_stream_chat(*args, **kwargs):
        reply = json.dumps(
            {
                "assistant_message": "¿Cuál es el proyecto específico?",
                "status": "collecting",
                "draft": None,
            }
        )
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", fake_stream_chat)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "expert",
            "messages": [{"role": "user", "content": specification}],
        },
    )

    done = next(
        event for event in _events(response.text) if event["type"] == "builder_done"
    )
    assert done["status"] == "ready"
    assert done["draft"]["system_prompt"] == specification.strip()


def test_guided_mode_recovers_from_invalid_model_json(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM guided",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()

    calls = 0

    async def fake_stream_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield 'data: {"type":"done","reply":"respuesta sin JSON"}\n\n'
            return
        reply = json.dumps(
            {
                "assistant_message": "He creado el agente.",
                "status": "ready",
                "draft": {
                    "name": "Especialista en soporte",
                    "description": "Ayuda a clientes",
                    "system_prompt": (
                        "Eres especialista en soporte. Responde con precisión, "
                        "empatía y sin inventar información."
                    ),
                    "temperature": 0.3,
                    "skills": [],
                    "knowledge": [],
                    "use_memory": False,
                },
            }
        )
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", fake_stream_chat)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [
                {"role": "user", "content": "Quiero ayudar a mis clientes"}
            ],
        },
    )

    done = next(
        event for event in _events(response.text) if event["type"] == "builder_done"
    )
    assert done["status"] == "ready"
    assert calls == 2


def test_guided_mode_falls_back_after_two_invalid_model_replies(
    admin_client, monkeypatch
):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM invalid output",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()

    calls = 0

    async def fake_stream_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        yield 'data: {"type":"done","reply":"respuesta sin JSON"}\n\n'

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", fake_stream_chat)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Créame un agente que programe en Java con buenas prácticas"
                    ),
                }
            ],
        },
    )

    events = _events(response.text)
    done = next(event for event in events if event["type"] == "builder_done")
    assert done["status"] == "ready"
    assert "Java" in done["draft"]["system_prompt"]
    assert not any(event["type"] == "error" for event in events)
    assert calls == 2


def test_complete_expert_specification_skips_provider(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM fast path",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    specification = (
        "Eres un agente senior especializado en Python y FastAPI. "
        "Conserva todos los requisitos técnicos, valida seguridad y añade pruebas. "
    ) * 5

    async def must_not_call_provider(*args, **kwargs):
        raise AssertionError("La especificación completa no debe llamar al proveedor")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "app.api.routes.agent_builder.stream_chat", must_not_call_provider
    )

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "expert",
            "messages": [{"role": "user", "content": specification}],
        },
    )

    done = next(
        event for event in _events(response.text) if event["type"] == "builder_done"
    )
    assert done["status"] == "ready"
    assert done["draft"]["name"] == "Especialista Python y FastAPI"
    assert done["draft"]["system_prompt"] == specification.strip()


def test_clear_guided_request_uses_provider(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM guided fast path",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()

    calls = 0

    async def fake_stream_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        reply = json.dumps(
            {
                "assistant_message": "Agente diseñado por el modelo.",
                "status": "ready",
                "draft": {
                    "name": "Especialista Java",
                    "description": "Programa Java con buenas prácticas",
                    "system_prompt": (
                        "Eres un especialista senior en Java. Aplica buenas prácticas, "
                        "seguridad, diseño mantenible y pruebas automatizadas."
                    ),
                    "temperature": 0.2,
                    "skills": [],
                    "knowledge": [],
                    "use_memory": False,
                },
            }
        )
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr(
        "app.api.routes.agent_builder.stream_chat", fake_stream_chat
    )

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Créame un agente que sepa programar en Java "
                        "con buenas prácticas"
                    ),
                }
            ],
        },
    )

    done = next(
        event for event in _events(response.text) if event["type"] == "builder_done"
    )
    assert done["status"] == "ready"
    assert done["draft"]["name"] == "Especialista Java"
    assert calls == 1


def test_builder_recovers_complete_json_before_provider_timeout(
    admin_client, monkeypatch
):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM partial response",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    reply = json.dumps(
        {
            "assistant_message": "Borrador listo.",
            "status": "ready",
            "draft": {
                "name": "Especialista en ciberseguridad",
                "description": "Analiza riesgos y recomienda controles",
                "system_prompt": (
                    "Eres especialista en ciberseguridad. Analiza riesgos, "
                    "protege datos y no facilites acciones maliciosas."
                ),
                "temperature": 0.2,
                "skills": [],
                "knowledge": [],
                "use_memory": False,
            },
        }
    )

    async def timeout_after_reply(*args, **kwargs):
        yield f"data: {json.dumps({'type': 'token', 'token': reply})}\n\n"
        yield 'data: {"type":"error","message":"The read operation timed out"}\n\n'

    monkeypatch.setattr(
        "app.api.routes.agent_builder.stream_chat", timeout_after_reply
    )

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [
                {
                    "role": "user",
                    "content": "Crea un agente especializado en ciberseguridad",
                }
            ],
        },
    )

    done = next(
        event for event in _events(response.text) if event["type"] == "builder_done"
    )
    assert done["status"] == "ready"
    assert done["draft"]["name"] == "Especialista en ciberseguridad"
