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

REGLAS OBLIGATORIAS:
- No converses, no hagas preguntas y no pidas más contexto.
- Usa tu conocimiento del dominio para inferir capacidades, buenas prácticas,
  proceso de trabajo, seguridad, límites y formato de respuesta.
- El system_prompt debe ser operativo y detallado, no una descripción comercial.
- Incluye qué debe hacer el agente, cómo debe trabajar, qué debe comprobar, qué
  no debe hacer y cómo debe presentar sus resultados.
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

Un buen system_prompt debe incluir: identidad y objetivo, flujo de trabajo,
preguntas que debe hacer cuando falte contexto, reglas y límites, y formato de
respuesta. Escribe instrucciones operativas, no una descripción comercial.
No marques el borrador como listo si el system_prompt es una frase breve o
genérica: debe poder guiar por sí solo el trabajo del agente.

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
    lower = text.lower()
    if "python" in lower and "fastapi" in lower:
        return "Especialista Python y FastAPI"
    if any(word in lower for word in ("cliente", "soporte", "atención")):
        return "Asistente de Atención al Cliente"
    if any(word in lower for word in ("contenido", "redes sociales", "linkedin")):
        return "Asistente de Contenidos"
    if any(word in lower for word in ("documento", "contrato", "pdf")):
        return "Analista de Documentos"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first_line = re.sub(
        r"^(quiero|necesito)\s+(crear\s+)?(un\s+)?agente\s+(que\s+|para\s+)?",
        "",
        first_line,
        flags=re.IGNORECASE,
    )
    words = first_line.split()
    candidate = " ".join(words[:7]).strip(" .,:;-")
    return candidate[:80] if candidate else "Asistente personalizado"


def _fallback_description(text: str) -> str:
    lower = text.lower()
    if "python" in lower and "fastapi" in lower:
        return (
            "Diseña, implementa y revisa backends profesionales con Python y FastAPI."
        )
    if any(word in lower for word in ("cliente", "soporte", "atención")):
        return "Ayuda a atender clientes con respuestas claras, fiables y coherentes."
    if any(word in lower for word in ("contenido", "redes sociales", "linkedin")):
        return "Planifica y crea contenido adaptado al público y al canal."
    if any(word in lower for word in ("documento", "contrato", "pdf")):
        return "Analiza documentos y presenta hallazgos de forma clara y verificable."
    first = next((answer for answer in text.splitlines() if answer.strip()), text)
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
    longest = max(answers, key=len, default="")
    if _is_actionable_system_prompt(longest):
        system_prompt = longest
    else:
        system_prompt = f"""Eres un asistente especializado en este objetivo:

{combined or "Ayudar al usuario con la tarea que indique."}

## Forma de trabajo

1. Identifica el resultado que necesita el usuario y las restricciones expresadas.
2. Solicita solamente la información imprescindible que falte para trabajar bien.
3. Ejecuta la tarea de forma ordenada, explicando decisiones relevantes.
4. Comprueba que el resultado responde al objetivo antes de entregarlo.

## Reglas y límites

- No inventes datos, fuentes, requisitos ni acciones realizadas.
- Distingue hechos, supuestos y recomendaciones cuando pueda haber confusión.
- Protege datos sensibles y avisa de riesgos o limitaciones importantes.
- Mantén el trabajo dentro del objetivo solicitado y pide confirmación antes de
  realizar acciones irreversibles o con impacto externo.

## Formato de respuesta

Entrega primero el resultado principal. Añade después los pasos, comprobaciones,
supuestos o siguientes acciones que ayuden al usuario a revisarlo y utilizarlo."""
    return BuilderEnvelope(
        assistant_message=(
            "He preparado un borrador con la información disponible. "
            "Puedes revisarlo y editarlo antes de crear el agente."
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
        return envelope
    final_error = quality_error or last_error
    detail = f": {final_error}" if final_error else ""
    raise ValueError(f"El proveedor no devolvió un borrador válido{detail}")
