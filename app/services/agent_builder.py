"""Conversation helpers for the AI-assisted agent builder."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError


class BuilderMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class BuilderResource(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class BuilderResources(BaseModel):
    skills: List[BuilderResource] = Field(default_factory=list, max_length=100)
    knowledge: List[BuilderResource] = Field(default_factory=list, max_length=100)


class AgentDraft(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    system_prompt: str = Field(min_length=20)
    model: str = Field(default="", max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    skills: List[str] = Field(default_factory=list, max_length=50)
    knowledge: List[str] = Field(default_factory=list, max_length=50)
    use_memory: bool = False


class BuilderEnvelope(BaseModel):
    assistant_message: str = Field(min_length=1, max_length=4_000)
    status: Literal["collecting", "ready"]
    draft: Optional[AgentDraft] = None


BuilderMode = Literal["auto", "guided", "expert"]

_MIN_ACTIONABLE_PROMPT_CHARS = 90
_PROFESSIONAL_PROMPT_CHARS = 450


def _is_actionable_system_prompt(prompt: str) -> bool:
    """Reject short or repetitive text that cannot guide an agent reliably."""
    clean = prompt.strip()
    words = re.findall(r"\w+", clean, flags=re.UNICODE)
    unique_words = {word.casefold() for word in words}
    return (
        len(clean) >= _MIN_ACTIONABLE_PROMPT_CHARS
        and len(words) >= 12
        and len(unique_words) >= 10
    )


def _is_professionally_structured(prompt: str) -> bool:
    """Return whether a prompt covers the operating areas of a reliable agent."""
    clean = prompt.strip().casefold()
    areas = (
        ("objetivo", "goal", "resultado", "outcome", "alcance", "scope"),
        ("flujo", "proceso", "workflow", "steps", "pasos", "procedimiento"),
        ("verifica", "valida", "quality", "calidad", "check", "comprueba"),
        ("límite", "limit", "no debes", "must not", "escal", "riesgo", "risk"),
        ("formato", "format", "estructura", "entrega", "response", "output"),
    )
    covered_areas = sum(any(term in clean for term in area) for area in areas)
    return len(prompt.strip()) >= _PROFESSIONAL_PROMPT_CHARS and covered_areas >= 4


def _ensure_professional_system_prompt(prompt: str) -> str:
    """Complete an actionable but shallow prompt with a dependable work contract."""
    clean = prompt.strip()
    if _is_professionally_structured(clean):
        return clean

    spanish_markers = re.findall(
        r"\b(?:eres|debes|usuario|respuesta|objetivo|datos|antes|cuando|para)\b",
        clean.casefold(),
    )
    english_markers = re.findall(
        r"\b(?:you|must|user|response|goal|data|before|when|for)\b",
        clean.casefold(),
    )
    if len(english_markers) > len(spanish_markers):
        framework = """## Operating method

1. Confirm the desired outcome, audience, available context, and constraints. Ask only for missing information that would materially change the result.
2. Plan the work before producing the final answer. Apply domain best practices and use available resources only when they are relevant.
3. Execute precisely. Never fabricate facts, sources, access, tool results, or actions. Clearly label assumptions and uncertainty.
4. Review the result against the user's goal, stated constraints, internal consistency, and practical usability before delivering it.

## Decision rules and boundaries

- Prioritize accuracy, clarity, privacy, and user intent over speed or apparent completeness.
- Distinguish verified facts from estimates and recommendations. Explain important trade-offs succinctly.
- Stay within the requested scope. Ask for confirmation before irreversible, sensitive, costly, or externally visible actions.
- If the request is unsafe, unsupported, or lacks critical evidence, explain the limitation and offer a safe next step.

## Response contract

Lead with the useful result. Then provide the evidence, decisions, checks, assumptions, or next actions needed to review and use it. Adapt depth and terminology to the user's expertise; avoid filler and generic claims."""
    else:
        framework = """## Método de trabajo

1. Confirma el resultado esperado, el destinatario, el contexto disponible y las restricciones. Pregunta solo por información ausente que cambie materialmente el resultado.
2. Planifica el trabajo antes de redactar la respuesta final. Aplica buenas prácticas del dominio y usa los recursos disponibles solo cuando sean relevantes.
3. Ejecuta con precisión. No inventes hechos, fuentes, accesos, resultados de herramientas ni acciones realizadas. Señala claramente supuestos e incertidumbres.
4. Revisa el resultado frente al objetivo, las restricciones indicadas, la coherencia interna y su utilidad práctica antes de entregarlo.

## Criterios de decisión y límites

- Prioriza precisión, claridad, privacidad e intención del usuario frente a rapidez o apariencia de completitud.
- Distingue hechos verificados, estimaciones y recomendaciones. Explica brevemente los trade-offs importantes.
- Mantente dentro del alcance solicitado. Pide confirmación antes de acciones irreversibles, sensibles, costosas o visibles externamente.
- Si la petición es insegura, no está soportada o carece de evidencia crítica, explica el límite y ofrece un siguiente paso seguro.

## Contrato de respuesta

Empieza por el resultado útil. Añade después evidencias, decisiones, comprobaciones, supuestos o siguientes acciones necesarios para revisarlo y utilizarlo. Adapta la profundidad y el vocabulario al nivel del usuario; evita relleno y afirmaciones genéricas."""
    return f"{clean}\n\n{framework}"


def should_force_ready(
    messages: List[BuilderMessage], mode: BuilderMode = "auto"
) -> bool:
    """Decide when another clarification would hurt more than it helps."""
    user_messages = [
        message.content for message in messages if message.role == "user"
    ]
    detailed = any(len(message.strip()) >= 500 for message in user_messages)
    if not user_messages:
        return False
    if mode == "expert":
        return True
    if mode == "guided":
        actionable = any(
            len(re.findall(r"\w+", message, flags=re.UNICODE)) >= 4
            for message in user_messages
        )
        return detailed or actionable or len(user_messages) >= 2
    return detailed or len(user_messages) >= 2


def can_build_without_model(
    messages: List[BuilderMessage], mode: BuilderMode
) -> bool:
    """A complete expert specification is already the agent's source of truth."""
    return mode == "expert" and any(
        message.role == "user" and len(message.content.strip()) >= 500
        for message in messages
    )


def build_system_prompt(
    resources: BuilderResources,
    *,
    force_ready: bool = False,
    mode: BuilderMode = "auto",
) -> str:
    """Return the builder prompt, including only the resource catalogue supplied."""

    def catalogue(items: Iterable[BuilderResource]) -> str:
        values = list(items)
        if not values:
            return "(ninguno disponible)"
        return "\n".join(f"- {item.name} [id: {item.id}]" for item in values)

    if force_ready:
        return f"""Eres un generador de agentes de iAgentsHub.

La petición del usuario ya es suficiente. Tu única tarea es diseñar AHORA un
agente completo y específico para la especialidad u objetivo solicitado.

ESTÁNDAR PROFESIONAL OBLIGATORIO:
- No converses, no hagas preguntas y no pidas más contexto.
- Usa tu conocimiento del dominio para inferir capacidades, buenas prácticas,
  proceso de trabajo, seguridad, límites y formato de respuesta.
- El system_prompt debe ser operativo, específico y autónomo, no una descripción
  comercial ni una lista de cualidades abstractas.
- Estructúralo con identidad y objetivo, alcance, método de trabajo paso a paso,
  criterios de decisión, comprobaciones de calidad, límites y contrato de salida.
- Define cómo actuar ante contexto insuficiente, incertidumbre, información
  contradictoria, riesgos y peticiones fuera de alcance.
- Indica cómo usar los recursos asignados y prohíbe fingir accesos, fuentes,
  verificaciones o acciones que no se hayan realizado realmente.
- Adapta profundidad, vocabulario y formato al destinatario y al tipo de tarea.
- No inventes requisitos del usuario, pero sí completa prácticas profesionales
  razonables y editables propias de la especialidad.
- Escribe todo en el idioma del usuario.

Skills disponibles:
{catalogue(resources.skills)}

Conocimiento disponible:
{catalogue(resources.knowledge)}

Solo puedes usar IDs presentes en esos catálogos. Si no hay un recurso claramente
relevante, deja su lista vacía.

Devuelve ÚNICAMENTE un objeto JSON válido con esta forma exacta:
{{
  "assistant_message": "He preparado el borrador para que puedas revisarlo.",
  "status": "ready",
  "draft": {{
    "name": "nombre específico y breve",
    "description": "qué consigue el agente",
    "system_prompt": "instrucciones profesionales completas",
    "model": "",
    "temperature": 0.4,
    "skills": [],
    "knowledge": [],
    "use_memory": false
  }}
}}

status="ready" es obligatorio y draft nunca puede ser null."""

    if mode == "guided":
        interview_rule = """
MODO GUIADO PARA PERSONAS NO TÉCNICAS:
- Habla con palabras cotidianas, frases cortas y tono cercano.
- Haz UNA sola pregunta cada vez y que pueda responderse en una frase.
- No preguntes por modelos, prompts, APIs, herramientas ni arquitectura.
- Una petición que identifica una especialidad, profesión, tema u objetivo ya
  es suficiente para crear el agente.
- En ese caso, infiere las capacidades, buenas prácticas, límites, proceso de
  trabajo y formato de respuesta propios de esa especialidad.
- No preguntes por proyecto, público, presupuesto, preferencias ni datos de
  pedido salvo que sean imprescindibles para el objetivo concreto.
- Solo usa status=collecting si el mensaje no permite saber qué debe hacer el
  agente. Como máximo haz UNA pregunta en toda la conversación.
- Nunca uses ejemplos de compras, pedidos o ventas para un agente de otra área.
- Completa los detalles técnicos con decisiones sensatas y editables."""
    elif mode == "expert":
        interview_rule = """
MODO TÉCNICO CON ESPECIFICACIÓN:
- Trata las instrucciones del usuario como fuente de verdad.
- Conserva requisitos, restricciones, proceso y formato de salida.
- Puedes reorganizarlos para mayor claridad, pero no los simplifiques ni
  elimines silenciosamente.
- No hagas preguntas: genera directamente un borrador completo."""
    else:
        interview_rule = """
MODO AUTOMÁTICO:
- Una especificación detallada debe generar un borrador directamente.
- Para una petición breve puedes hacer como máximo UNA pregunta corta."""

    return f"""Eres el Constructor de Agentes de iAgentsHub.

Tu trabajo es conversar brevemente con el usuario y preparar un agente útil,
seguro y específico. Pregunta solo por datos que cambien materialmente el
resultado. Debes conocer como mínimo el objetivo, las tareas principales y los
límites. Si el primer mensaje ya contiene suficiente información, puedes crear
el borrador sin hacer preguntas innecesarias.
{interview_rule}

Un system_prompt profesional debe incluir: identidad, objetivo y resultados;
alcance; flujo de trabajo paso a paso; criterios para decidir y preguntar;
uso correcto de skills y conocimiento; comprobaciones de calidad; privacidad,
seguridad, límites y escalado; y un contrato de respuesta adaptado al usuario.
Debe distinguir hechos, supuestos y recomendaciones, resolver instrucciones
contradictorias con prudencia y prohibir fingir fuentes, accesos o acciones.
Escribe instrucciones observables y operativas, no una descripción comercial
ni una colección de adjetivos como "útil", "experto" o "profesional".
No marques el borrador como listo si el system_prompt es una frase breve o
genérica: debe poder guiar por sí solo el trabajo del agente.

El nombre debe ser breve y específico. La descripción debe explicar en una sola
frase el resultado que obtiene el usuario, no repetir el nombre. Ajusta la
temperatura al trabajo: 0.2-0.4 para precisión y procesos; 0.6-0.8 solo cuando
la creatividad sea parte central del objetivo.

Skills disponibles:
{catalogue(resources.skills)}

Conocimiento disponible:
{catalogue(resources.knowledge)}

Solo puedes usar IDs presentes en esos catálogos. No inventes IDs. Si no hay un
recurso claramente relevante, deja la lista vacía.

Responde SIEMPRE con un único objeto JSON válido, sin Markdown ni texto fuera
del JSON, con esta forma:
{{
  "assistant_message": "mensaje visible para el usuario",
  "status": "collecting" o "ready",
  "draft": null o {{
    "name": "nombre breve",
    "description": "descripción",
    "system_prompt": "instrucciones completas",
    "model": "",
    "temperature": 0.7,
    "skills": [],
    "knowledge": [],
    "use_memory": false
  }}
}}

Usa status=collecting y draft=null cuando necesites una respuesta. Usa
status=ready y un draft completo cuando ya puedas proponer el agente. El mensaje
visible debe estar en el idioma del usuario."""


def _fallback_name(text: str) -> str:
    """Derive the name from what the user wrote, never from a guessed domain."""
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    # Una tabla de palabras clave bautizaba "Asistente de Atención al Cliente"
    # cualquier petición que mencionase "cliente", aunque fuese para redactar
    # correos comerciales. Un nombre derivado y soso es editable; uno
    # confiadamente equivocado se cuela hasta el agente creado.
    sentence = first_line.split(".")[0]
    sentence = re.sub(
        r"^(?:(?:quiero|necesito)\s+(?:crear\s+)?(?:un\s+)?agente\s+(?:que\s+|para\s+)?"
        r"|eres\s+(?:un|una)\s+(?:agente|asistente)\s+(?:de\s+|para\s+)?)"
        r"(?:me\s+ayud\w+\s+(?:a|con)\s+)?",
        "",
        sentence,
        flags=re.IGNORECASE,
    )
    candidate = " ".join(sentence.split()[:7]).strip(" .,:;-")
    return candidate[:80] if candidate else "Asistente personalizado"


def _fallback_description(text: str) -> str:
    first = next((line for line in text.splitlines() if line.strip()), text)
    return f"Asistente creado para: {first.strip()[:300]}"


def build_fallback_ready(
    messages: List[BuilderMessage],
    resources: BuilderResources,
    mode: BuilderMode,
) -> BuilderEnvelope:
    """Create a safe draft when a small model ignores or breaks the JSON contract."""
    answers = [
        message.content.strip()
        for message in messages
        if message.role == "user" and message.content.strip()
    ]
    combined = "\n\n".join(answers)
    if mode == "expert" and _is_actionable_system_prompt(combined):
        # En modo técnico el usuario escribió la especificación a propósito: es
        # la fuente de verdad y se conserva tal cual.
        system_prompt = combined
    else:
        # En los demás modos el usuario escribió una petición, no instrucciones.
        # Usarla tal cual dejaba system_prompts en primera persona ("quiero un
        # agente que me ayude..."), que describen al solicitante y no instruyen
        # a nadie; y se saltaba el marco profesional que parse_builder_reply sí
        # aplica a todo borrador venido del modelo.
        system_prompt = _ensure_professional_system_prompt(
            "Eres un asistente especializado en este objetivo:\n\n"
            f"{combined or 'Ayudar al usuario con la tarea que indique.'}"
        )
    return BuilderEnvelope(
        assistant_message=(
            "He preparado un borrador editable a partir de tu petición, sin "
            "pasar por el modelo. Revísalo antes de crear el agente."
        ),
        status="ready",
        draft=AgentDraft(
            name=_fallback_name(combined),
            description=(
                _fallback_description(combined)
                if answers
                else "Asistente personalizado"
            ),
            system_prompt=system_prompt,
            temperature=0.4,
            skills=[],
            knowledge=[],
            use_memory=False,
        ),
    )


def _json_objects(text: str) -> Iterable[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def parse_builder_reply(
    reply: str, resources: BuilderResources
) -> BuilderEnvelope:
    """Extract, validate and sanitize the structured response from the model."""
    last_error: Exception | None = None
    quality_error: Exception | None = None
    for value in _json_objects(reply):
        try:
            envelope = BuilderEnvelope.model_validate(value)
        except ValidationError as exc:
            last_error = exc
            continue
        if envelope.status == "ready" and envelope.draft is None:
            last_error = ValueError("El modelo marcó el borrador como listo sin incluirlo")
            continue
        if (
            envelope.status == "ready"
            and envelope.draft is not None
            and not _is_actionable_system_prompt(envelope.draft.system_prompt)
        ):
            quality_error = ValueError(
                "El system_prompt es demasiado breve o genérico para guiar al agente"
            )
            continue
        if envelope.draft is not None:
            allowed_skills = {item.id for item in resources.skills}
            allowed_knowledge = {item.id for item in resources.knowledge}
            envelope.draft.skills = [
                item for item in envelope.draft.skills if item in allowed_skills
            ]
            envelope.draft.knowledge = [
                item
                for item in envelope.draft.knowledge
                if item in allowed_knowledge
            ]
            envelope.draft.system_prompt = _ensure_professional_system_prompt(
                envelope.draft.system_prompt
            )
        return envelope
    final_error = quality_error or last_error
    detail = f": {final_error}" if final_error else ""
    raise ValueError(f"El proveedor no devolvió un borrador válido{detail}")
