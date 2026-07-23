from __future__ import annotations

import pytest

from app.services.agent_builder import (
    BuilderMessage,
    BuilderResource,
    BuilderResources,
    build_fallback_ready,
    build_system_prompt,
    can_build_without_model,
    guided_recovery_question,
    parse_builder_reply,
    should_force_ready,
)


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
        "system_prompt": "Eres un agente de soporte. Pregunta por el contexto antes de responder.",
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


def test_force_ready_prompt_forbids_more_questions():
    prompt = build_system_prompt(BuilderResources(), force_ready=True)

    assert "PROHIBIDO hacer otra pregunta" in prompt
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


def test_guided_mode_allows_two_short_clarifications():
    one_turn = [BuilderMessage(role="user", content="Quiero ayudar a mis clientes")]
    two_turns = [
        *one_turn,
        BuilderMessage(role="assistant", content="¿Qué resultado necesitan?"),
        BuilderMessage(role="user", content="Respuestas claras a sus dudas"),
    ]
    three_turns = [
        *two_turns,
        BuilderMessage(role="assistant", content="¿Qué debe evitar?"),
        BuilderMessage(role="user", content="No debe inventar información"),
    ]

    assert should_force_ready(one_turn, "guided") is False
    assert should_force_ready(two_turns, "guided") is False
    assert should_force_ready(three_turns, "guided") is True


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
    assert envelope.draft.name == "Especialista Python y FastAPI"
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
    assert "No inventes datos" in envelope.draft.system_prompt


def test_guided_recovery_question_is_short_and_concrete():
    envelope = guided_recovery_question(
        [BuilderMessage(role="user", content="Quiero un asistente")]
    )

    assert envelope.status == "collecting"
    assert envelope.draft is None
    assert "¿Quién usará" in envelope.assistant_message


def test_guided_question_uses_the_goal_already_provided():
    envelope = guided_recovery_question(
        [
            BuilderMessage(
                role="user",
                content="Quiero un agente que ayude a mis clientes a elegir el producto adecuado",
            )
        ]
    )

    assert "¿Qué debería preguntar" in envelope.assistant_message
    assert envelope.assistant_message.count("?") == 1
