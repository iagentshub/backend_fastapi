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


def test_builder_preserva_codigo_de_credencial_ilegible(admin_client, monkeypatch):
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

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", unreadable)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "auto",
            "messages": [{"role": "user", "content": "Ayúdame"}],
        },
    )

    error = next(event for event in _events(response.text) if event["type"] == "error")
    assert error["code"] == "credential_unreadable"


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


def test_progress_reports_stage_and_partial_message(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM progreso",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()

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

    async def token_by_token(*args, **kwargs):
        for start in range(0, len(reply), 16):
            token = reply[start : start + 16]
            yield f"data: {json.dumps({'type': 'token', 'token': token})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", token_by_token)

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
    progress = [event for event in events if event["type"] == "progress"]
    assert progress, "el constructor debe informar avance mientras redacta"

    stages = [event["stage"] for event in progress]
    assert stages[0] == "analyzing"
    assert "replying" in stages
    assert stages[-1] == "writing_instructions"
    # Las etapas nunca retroceden y no se repite el mismo payload dos veces.
    order = ["analyzing", "replying", "drafting", "writing_instructions"]
    assert stages == sorted(stages, key=order.index)
    unique = {json.dumps(event, sort_keys=True) for event in progress}
    assert len(progress) == len(unique)

    partials = [event["assistant_message"] for event in progress]
    assert any(partials), "el mensaje visible debe llegar antes que el borrador"
    visible = next(text for text in partials if text)
    assert "He preparado el borrador.".startswith(visible)

    done = next(event for event in events if event["type"] == "builder_done")
    assert done["assistant_message"] == "He preparado el borrador."
    assert done["draft"]["name"] == "Agente de pruebas"


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
            "messages": [{"role": "user", "content": "Quiero ayudar a mis clientes"}],
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
    assert done["draft"]["name"] == "senior especializado en Python y FastAPI"
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

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", timeout_after_reply)

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


def test_builder_retries_http_529_and_uses_second_response(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM overloaded once",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    calls = 0

    async def overloaded_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield (
                'data: {"type":"error","message":'
                '"HTTP 529: Service temporarily overloaded"}\n\n'
            )
            return
        reply = json.dumps(
            {
                "assistant_message": "Borrador listo.",
                "status": "ready",
                "draft": {
                    "name": "Especialista Java",
                    "description": "Programa Java con buenas prácticas",
                    "system_prompt": (
                        "Eres especialista senior en Java. Analiza los requisitos "
                        "antes de proponer una solución, aplica diseño mantenible, "
                        "valida entradas, protege datos sensibles y escribe pruebas "
                        "automatizadas. Explica las decisiones importantes, señala "
                        "suposiciones y nunca inventes resultados de una ejecución."
                    ),
                    "temperature": 0.2,
                    "skills": [],
                    "knowledge": [],
                    "use_memory": False,
                },
            },
            ensure_ascii=False,
        )
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", overloaded_once)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [{"role": "user", "content": "Quiero programar en Java"}],
        },
    )

    events = _events(response.text)
    done = next(event for event in events if event["type"] == "builder_done")
    assert done["draft"]["name"] == "Especialista Java"
    assert calls == 2
    assert not any(event["type"] == "error" for event in events)


def test_builder_falls_back_when_http_529_persists(admin_client, monkeypatch):
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM overloaded",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    calls = 0

    async def always_overloaded(*args, **kwargs):
        nonlocal calls
        calls += 1
        yield (
            'data: {"type":"error","message":'
            '"HTTP 529: Service temporarily overloaded"}\n\n'
        )

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", always_overloaded)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [{"role": "user", "content": "Quiero programar en Java"}],
        },
    )

    events = _events(response.text)
    done = next(event for event in events if event["type"] == "builder_done")
    assert done["status"] == "ready"
    assert "Java" in done["draft"]["system_prompt"]
    assert calls == 2
    assert not any(event["type"] == "error" for event in events)


def test_builder_reintenta_un_proveedor_inalcanzable(admin_client, monkeypatch):
    """Los fallos transitorios se reconocen por el código, no por el texto.

    La clasificación buscaba subcadenas ("timeout", "capacity") que ningún
    mensaje de stream_chat contiene, así que la capacidad agotada y el
    proveedor inalcanzable —los dos fallos pasajeros más frecuentes— se
    trataban como definitivos y no se reintentaban nunca.
    """
    connection = admin_client.post(
        "/api/connections",
        json={
            "name": "NIM caido una vez",
            "type": "nvidia",
            "api_key": "nvapi-test",
            "model": "meta/llama-3.2-3b-instruct",
        },
    ).json()
    calls = 0

    async def unreachable_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield (
                'data: {"type":"error","code":"provider_unreachable",'
                '"message":"No se pudo contactar con el proveedor."}\n\n'
            )
            return
        reply = json.dumps(
            {
                "assistant_message": "Borrador listo.",
                "status": "ready",
                "draft": {
                    "name": "Especialista Java",
                    "description": "Programa Java con buenas prácticas",
                    "system_prompt": (
                        "Eres especialista senior en Java. Analiza los requisitos "
                        "antes de proponer una solución, aplica diseño mantenible, "
                        "valida entradas, protege datos sensibles y escribe pruebas "
                        "automatizadas. Explica las decisiones importantes, señala "
                        "suposiciones y nunca inventes resultados de una ejecución."
                    ),
                    "temperature": 0.2,
                    "skills": [],
                    "knowledge": [],
                    "use_memory": False,
                },
            },
            ensure_ascii=False,
        )
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    monkeypatch.setattr("app.api.routes.agent_builder.stream_chat", unreachable_once)

    response = admin_client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": connection["id"],
            "mode": "guided",
            "messages": [{"role": "user", "content": "Quiero programar en Java"}],
        },
    )

    events = _events(response.text)
    done = next(event for event in events if event["type"] == "builder_done")
    assert done["draft"]["name"] == "Especialista Java"
    assert calls == 2
    assert not any(event["type"] == "error" for event in events)
