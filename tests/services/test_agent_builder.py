from __future__ import annotations

import json

import pytest

from app.services.agent_builder import (
    AgentDraft,
    BuilderMessage,
    BuilderResource,
    BuilderResources,
    build_fallback_ready,
    build_system_prompt,
    can_build_without_model,
    parse_builder_reply,
    should_force_ready,
)


def test_builder_accepts_unlimited_message_and_prompt_content():
    message = BuilderMessage(role="user", content="x" * 50_000)
    draft = AgentDraft(name="Agente grande", system_prompt="x" * 50_000)

    assert len(message.content) == 50_000
    assert len(draft.system_prompt) == 50_000


def test_parse_ready_draft_and_remove_unknown_resource_ids():
    resources = BuilderResources(
        skills=[BuilderResource(id="known-skill", name="Skill conocida")],
        knowledge=[BuilderResource(id="known-doc", name="Documento conocido")],
    )
    reply = """
    ```json
    {
      "assistant_message": "Ya está listo.",
      "status": "ready",
      "draft": {
        "name": "Agente de soporte",
        "description": "Responde dudas",
        "system_prompt": "Eres un agente de soporte. Pregunta por el contexto antes de responder, verifica los datos disponibles y explica con claridad cualquier límite o incertidumbre.",
        "temperature": 0.4,
        "skills": ["known-skill", "invented-skill"],
        "knowledge": ["known-doc", "other-doc"],
        "use_memory": true
      }
    }
    ```
    """

    result = parse_builder_reply(reply, resources)

    assert result.status == "ready"
    assert result.draft is not None
    assert result.draft.skills == ["known-skill"]
    assert result.draft.knowledge == ["known-doc"]


def test_parse_collecting_reply():
    result = parse_builder_reply(
        '{"assistant_message":"¿Quién usará el agente?","status":"collecting","draft":null}',
        BuilderResources(),
    )

    assert result.status == "collecting"
    assert result.draft is None


def test_ready_reply_requires_draft():
    with pytest.raises(ValueError, match="borrador"):
        parse_builder_reply(
            '{"assistant_message":"Listo","status":"ready","draft":null}',
            BuilderResources(),
        )


def test_system_prompt_contains_only_supplied_catalogue():
    prompt = build_system_prompt(
        BuilderResources(
            skills=[BuilderResource(id="skill-1", name="Investigación")],
            knowledge=[],
        )
    )

    assert "Investigación [id: skill-1]" in prompt
    assert "No inventes IDs" in prompt


def test_builder_requires_a_professional_operating_standard():
    prompt = build_system_prompt(BuilderResources(), force_ready=True)

    assert "ESTÁNDAR PROFESIONAL OBLIGATORIO" in prompt
    assert "comprobaciones de calidad" in prompt
    assert "prohíbe fingir accesos" in prompt
    assert "contrato de salida" in prompt


def test_force_ready_prompt_forbids_more_questions():
    prompt = build_system_prompt(BuilderResources(), force_ready=True)

    assert "No converses, no hagas preguntas" in prompt
    assert 'status="ready"' in prompt


def test_detailed_request_forces_draft_without_questions():
    messages = [BuilderMessage(role="user", content="x" * 500)]

    assert should_force_ready(messages) is True


def test_second_user_turn_forces_draft():
    messages = [
        BuilderMessage(role="user", content="Quiero un agente de Python"),
        BuilderMessage(role="assistant", content="¿Para qué tipo de proyecto?"),
        BuilderMessage(role="user", content="Para desarrollar APIs con FastAPI"),
    ]

    assert should_force_ready(messages) is True


def test_expert_mode_generates_on_first_turn():
    messages = [BuilderMessage(role="user", content="Implementa APIs con FastAPI")]

    assert should_force_ready(messages, "expert") is True


def test_complete_expert_specification_does_not_need_model():
    messages = [BuilderMessage(role="user", content="x" * 500)]

    assert can_build_without_model(messages, "expert") is True
    assert can_build_without_model(messages, "guided") is False


def test_clear_guided_request_forces_ready_model_response():
    messages = [
        BuilderMessage(
            role="user",
            content="Crea un agente especializado en ciberseguridad",
        )
    ]

    assert should_force_ready(messages, "guided") is True
    assert can_build_without_model(messages, "guided") is False


def test_guided_mode_allogroup_two_short_clarifications():
    vague_turn = [BuilderMessage(role="user", content="Ayúdame")]
    actionable_turn = [
        BuilderMessage(role="user", content="Quiero ayudar a mis clientes")
    ]
    two_turns = [
        *vague_turn,
        BuilderMessage(role="assistant", content="¿Qué resultado necesitan?"),
        BuilderMessage(role="user", content="Respuestas claras a sus dudas"),
    ]

    assert should_force_ready(vague_turn, "guided") is False
    assert should_force_ready(actionable_turn, "guided") is True
    assert should_force_ready(two_turns, "guided") is True


def test_guided_prompt_uses_plain_language_and_avoids_technical_questions():
    prompt = build_system_prompt(BuilderResources(), mode="guided")

    assert "palabras cotidianas" in prompt
    assert "UNA sola pregunta" in prompt
    assert "No preguntes por modelos" in prompt


def test_expert_fallback_preserves_full_specification():
    specification = (
        "Eres un agente senior de Python y FastAPI. "
        "Debes revisar seguridad, rendimiento y pruebas." * 8
    )
    envelope = build_fallback_ready(
        [BuilderMessage(role="user", content=specification)],
        BuilderResources(),
        "expert",
    )

    assert envelope.status == "ready"
    assert envelope.draft is not None
    assert envelope.draft.name == "senior de Python y FastAPI"
    assert envelope.draft.system_prompt == specification


def test_guided_fallback_turns_answers_into_actionable_prompt():
    envelope = build_fallback_ready(
        [
            BuilderMessage(role="user", content="Quiero responder dudas de clientes"),
            BuilderMessage(role="assistant", content="¿Qué resultado deben recibir?"),
            BuilderMessage(role="user", content="Una respuesta breve y amable"),
            BuilderMessage(role="assistant", content="¿Qué debe evitar?"),
            BuilderMessage(role="user", content="No inventar datos"),
        ],
        BuilderResources(),
        "guided",
    )

    assert envelope.draft is not None
    assert "Quiero responder dudas de clientes" in envelope.draft.system_prompt
    assert "No inventar datos" in envelope.draft.system_prompt
    # El borrador local reutiliza el mismo marco profesional que
    # parse_builder_reply aplica a los borradores del modelo.
    assert envelope.draft.system_prompt.startswith(
        "Eres un asistente especializado en este objetivo:"
    )
    assert "No inventes hechos" in envelope.draft.system_prompt
    assert "## Método de trabajo" in envelope.draft.system_prompt
    assert "## Criterios de decisión y límites" in envelope.draft.system_prompt
    assert "## Contrato de respuesta" in envelope.draft.system_prompt


def test_ready_draft_rejects_a_short_generic_system_prompt():
    reply = json.dumps(
        {
            "assistant_message": "Listo",
            "status": "ready",
            "draft": {
                "name": "Agente genérico",
                "system_prompt": "Eres un asistente útil que ayuda al usuario con sus tareas.",
            },
        }
    )

    with pytest.raises(ValueError, match="demasiado breve o genérico"):
        parse_builder_reply(reply, BuilderResources())


def test_actionable_but_shallow_draft_gets_professional_safeguards():
    original = (
        "Eres especialista en soporte técnico. Diagnostica cada incidencia, "
        "explica la solución con claridad y comprueba que resuelve el problema."
    )
    reply = json.dumps(
        {
            "assistant_message": "Borrador listo",
            "status": "ready",
            "draft": {
                "name": "Especialista en soporte técnico",
                "description": "Resuelve incidencias técnicas de forma fiable.",
                "system_prompt": original,
            },
        },
        ensure_ascii=False,
    )

    result = parse_builder_reply(reply, BuilderResources())

    assert result.draft is not None
    assert result.draft.system_prompt.startswith(original)
    assert "## Método de trabajo" in result.draft.system_prompt
    assert "## Criterios de decisión y límites" in result.draft.system_prompt
    assert "## Contrato de respuesta" in result.draft.system_prompt
    assert len(result.draft.system_prompt) > 900


def test_professional_prompt_is_preserved_without_duplicate_framework():
    professional = """You are a senior research analyst focused on useful outcomes.

## Scope and workflow
Confirm the goal and constraints, then plan the steps and execute the analysis.
Use only relevant evidence and clearly separate facts from assumptions.

## Quality checks and limits
Verify calculations, sources, internal consistency, and coverage before delivery.
Never invent evidence. Explain uncertainty, risk, and anything outside your scope.

## Response format
Lead with the result, then provide evidence, decisions, checks, and next steps.
Adapt the level of detail to the user and keep the output directly actionable.
"""
    reply = json.dumps(
        {
            "assistant_message": "Draft ready",
            "status": "ready",
            "draft": {
                "name": "Senior research analyst",
                "description": "Produces evidence-based research briefs.",
                "system_prompt": professional,
            },
        }
    )

    result = parse_builder_reply(reply, BuilderResources())

    assert result.draft is not None
    assert result.draft.system_prompt == professional.strip()


def test_fallback_no_convierte_la_peticion_en_system_prompt():
    """La petición se enmarca como objetivo; nunca se copia tal cual.

    Copiada literalmente dejaba un prompt en primera persona ("quiero un agente
    que me ayude...") que describe al solicitante en vez de instruir al agente,
    y una tabla de palabras clave lo bautizaba por la primera palabra que
    reconociese: "cliente" convertía una petición de redacción comercial en un
    agente de atención al cliente.
    """
    peticion = (
        "Quiero un agente que me ayude a redactar correos comerciales para los "
        "clientes de mi tienda de bicicletas, con tono cercano."
    )
    envelope = build_fallback_ready(
        [BuilderMessage(role="user", content=peticion)],
        BuilderResources(),
        "auto",
    )

    assert envelope.draft is not None
    prompt = envelope.draft.system_prompt
    assert prompt != peticion
    assert prompt.startswith("Eres un asistente especializado en este objetivo:")
    assert peticion in prompt
    assert "## Método de trabajo" in prompt
    assert "Atención al Cliente" not in envelope.draft.name
    # El usuario tiene que saber que el borrador no lo diseñó el modelo.
    assert "sin pasar por el modelo" in envelope.assistant_message
