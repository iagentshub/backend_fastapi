"""Structured prompts and validation for the AI-assisted skill builder."""

from __future__ import annotations

import json
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

SkillBuilderMode = Literal["guided", "expert"]
SkillCategory = Literal[
    "ai",
    "messaging",
    "notes",
    "productivity",
    "dev",
    "security",
    "media",
    "data",
    "company",
]
_CATEGORIES = {
    "ai",
    "messaging",
    "notes",
    "productivity",
    "dev",
    "security",
    "media",
    "data",
    "company",
}
_CATEGORY_ALIASES = {
    "development": "dev",
    "developer": "dev",
    "programming": "dev",
    "code": "dev",
    "cybersecurity": "security",
    "business": "company",
    "general": "productivity",
}

_MIN_ACTIONABLE_SKILL_CHARS = 180


def _is_actionable_skill_content(content: str) -> bool:
    """Require enough structure to make the skill reusable and checkable."""
    clean = content.strip()
    headings = re.findall(r"(?m)^#{1,3}\s+\S+", clean)
    procedure_items = re.findall(r"(?m)^\s*(?:\d+\.|[-*])\s+\S+", clean)
    return (
        len(clean) >= _MIN_ACTIONABLE_SKILL_CHARS
        and len(headings) >= 3
        and len(procedure_items) >= 3
    )


class SkillBuilderMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class SkillDraft(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    category: SkillCategory = "productivity"
    icon: str = Field(default="productivity", min_length=1, max_length=80)
    content: str = Field(min_length=40)


class SkillBuilderEnvelope(BaseModel):
    assistant_message: str = Field(min_length=1, max_length=2_000)
    status: Literal["collecting", "ready"]
    draft: Optional[SkillDraft] = None


def should_force_ready(
    messages: List[SkillBuilderMessage], mode: SkillBuilderMode
) -> bool:
    """A clear purpose is enough; the assistant must not over-interview the user."""
    user_messages = [
        message.content.strip()
        for message in messages
        if message.role == "user" and message.content.strip()
    ]
    if not user_messages:
        return False
    if mode == "expert":
        return True
    return (
        any(len(re.findall(r"\w+", message, flags=re.UNICODE)) >= 4 for message in user_messages)
        or len(user_messages) >= 2
    )


def build_from_skill_markdown(
    messages: List[SkillBuilderMessage],
) -> Optional[SkillBuilderEnvelope]:
    """Convert an already complete SKILL.md locally, without invoking an LLM."""
    user_content = "\n\n".join(
        message.content.strip()
        for message in messages
        if message.role == "user" and message.content.strip()
    )
    match = re.match(
        r"\A---\s*\r?\n(?P<frontmatter>.*?)\r?\n---\s*\r?\n(?P<body>.*)\Z",
        user_content,
        flags=re.DOTALL,
    )
    if not match:
        return None

    frontmatter = match.group("frontmatter")

    def field(name: str) -> str:
        value = re.search(
            rf"(?mi)^{re.escape(name)}:\s*(.+?)\s*$",
            frontmatter,
        )
        return value.group(1).strip().strip("\"'") if value else ""

    name = field("name")
    body = match.group("body").strip()
    if not name or not body:
        return None
    description = field("description") or f"Instrucciones reutilizables para {name}."
    return SkillBuilderEnvelope(
        assistant_message="He importado la skill completa sin modificar sus instrucciones.",
        status="ready",
        draft=SkillDraft(
            name=name,
            description=description,
            content=body,
        ),
    )


def build_system_prompt(*, force_ready: bool, mode: SkillBuilderMode) -> str:
    """Build a domain-neutral prompt that produces reusable skill instructions."""
    categories = (
        "ai, messaging, notes, productivity, dev, security, media, data, company"
    )
    if force_ready:
        interaction = """
La petición ya es suficiente. No hagas preguntas. Diseña ahora la skill completa
usando tu conocimiento profesional del tema solicitado."""
    elif mode == "guided":
        interaction = """
Si no se entiende la finalidad de la skill, haz UNA sola pregunta breve y
cotidiana. No preguntes por modelos, prompts, APIs ni arquitectura. En cuanto
se identifique una tarea, especialidad u objetivo, crea el borrador."""
    else:
        interaction = """
Las instrucciones del usuario son la fuente de verdad. No hagas preguntas:
organízalas y conviértelas directamente en una skill completa."""

    return f"""Eres el Constructor de Skills de iAgentsHub.

Una skill es una capacidad reutilizable y enfocada que enseña a un agente cómo
realizar bien una tarea. No es una personalidad completa ni un agente nuevo.
{interaction}

REGLAS:
- Escribe en el idioma del usuario.
- No inventes conexiones, archivos, endpoints ni recursos que no se mencionen.
- Completa buenas prácticas profesionales propias del tema sin cambiar el
  objetivo solicitado.
- Construye las instrucciones mediante campos breves y listas. El backend las
  convertirá a Markdown. No escribas saltos de línea dentro de strings JSON.
- Da instrucciones concretas y comprobables; evita texto comercial y relleno.
- Incluye situaciones de uso, entradas, al menos tres pasos concretos,
  comprobaciones, límites y un resultado esperado.
- Categorías permitidas: {categories}.
- icon debe coincidir con category.

Devuelve ÚNICAMENTE un objeto JSON válido, sin Markdown externo, con esta forma:
{{
  "assistant_message": "mensaje breve para el usuario",
  "status": "collecting" o "ready",
  "draft": null o {{
    "name": "nombre breve",
    "description": "qué capacidad aporta",
    "category": "una categoría permitida",
    "icon": "la misma categoría",
    "purpose": "objetivo operativo",
    "when_to_use": ["situación concreta"],
    "inputs": ["dato o contexto necesario"],
    "steps": ["paso concreto y ordenado"],
    "checks": ["comprobación antes de entregar"],
    "limits": ["acción que debe evitar"],
    "output": "formato esperado del resultado"
  }}
}}

Usa status=collecting y draft=null solo si resulta imposible saber qué tarea
debe realizar. Si status es ready, draft debe estar completo."""


def parse_builder_reply(reply: str) -> SkillBuilderEnvelope:
    """Extract the first valid structured envelope from a provider response."""
    decoder = json.JSONDecoder()
    last_error: Exception | None = None
    quality_error: Exception | None = None
    for index, char in enumerate(reply):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(reply[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        raw_draft = value.get("draft")
        if isinstance(raw_draft, dict):
            raw_category = str(raw_draft.get("category") or "productivity").lower()
            category = _CATEGORY_ALIASES.get(raw_category, raw_category)
            if category not in _CATEGORIES:
                category = "productivity"
            raw_draft["category"] = category
            raw_draft["icon"] = category
            if not raw_draft.get("content") and raw_draft.get("purpose"):
                required_lists = (
                    "when_to_use",
                    "inputs",
                    "steps",
                    "checks",
                    "limits",
                )
                missing = [
                    field
                    for field in required_lists
                    if not isinstance(raw_draft.get(field), list)
                    or not any(str(item).strip() for item in raw_draft[field])
                ]
                if len(raw_draft.get("steps") or []) < 3:
                    missing.append("steps (mínimo 3)")
                if not str(raw_draft.get("output") or "").strip():
                    missing.append("output")
                if missing:
                    quality_error = ValueError(
                        "La skill no incluye instrucciones operativas completas: "
                        + ", ".join(dict.fromkeys(missing))
                    )
                    continue

                def section(title: str, field: str) -> str:
                    items = raw_draft.get(field)
                    if not isinstance(items, list):
                        return ""
                    clean = [str(item).strip() for item in items if str(item).strip()]
                    return (
                        f"\n\n## {title}\n\n"
                        + "\n".join(f"- {item}" for item in clean)
                        if clean
                        else ""
                    )

                purpose = str(raw_draft.get("purpose") or "").strip()
                output = str(raw_draft.get("output") or "").strip()
                raw_draft["content"] = (
                    f"# Objetivo\n\n{purpose}"
                    + section("Cuándo usarla", "when_to_use")
                    + section("Entradas necesarias", "inputs")
                    + (
                        "\n\n## Procedimiento\n\n"
                        + "\n".join(
                            f"{index}. {str(item).strip()}"
                            for index, item in enumerate(
                                raw_draft.get("steps") or [], start=1
                            )
                            if str(item).strip()
                        )
                    )
                    + section("Comprobaciones", "checks")
                    + section("Límites", "limits")
                    + (f"\n\n## Resultado\n\n{output}" if output else "")
                )
        try:
            envelope = SkillBuilderEnvelope.model_validate(value)
        except ValidationError as exc:
            last_error = exc
            continue
        if envelope.status == "ready" and envelope.draft is None:
            last_error = ValueError("La respuesta no incluye el borrador")
            continue
        if (
            envelope.status == "ready"
            and envelope.draft is not None
            and not _is_actionable_skill_content(envelope.draft.content)
        ):
            quality_error = ValueError(
                "El contenido de la skill es demasiado breve o poco operativo"
            )
            continue
        if envelope.draft:
            envelope.draft.icon = envelope.draft.category
        return envelope
    final_error = quality_error or last_error
    detail = f": {final_error}" if final_error else ""
    raise ValueError(f"El proveedor no devolvió una skill válida{detail}")


def build_fallback_ready(
    messages: List[SkillBuilderMessage],
) -> SkillBuilderEnvelope:
    """Return an editable draft if the provider times out after receiving a clear task."""
    requests = [
        message.content.strip()
        for message in messages
        if message.role == "user" and message.content.strip()
    ]
    request = "\n\n".join(requests)
    first_line = next((line.strip() for line in request.splitlines() if line.strip()), "")
    cleaned = re.sub(
        r"^((quiero|necesito)\s+)?(crea(r)?\s+)?(una\s+)?skill\s+(que\s+|para\s+)?",
        "",
        first_line,
        flags=re.IGNORECASE,
    ).strip(" .,:;-")
    name = " ".join(cleaned.split()[:7])[:100] or "Skill personalizada"
    content = f"""# Objetivo

Ayudar a realizar correctamente la siguiente tarea:

{request}

## Cuándo usarla

Úsala cuando el usuario solicite esta capacidad o una tarea directamente relacionada.

## Procedimiento

1. Confirma únicamente los datos imprescindibles que falten.
2. Analiza el objetivo y las restricciones indicadas.
3. Realiza la tarea de forma ordenada, clara y verificable.
4. Revisa el resultado antes de entregarlo.

## Comprobaciones

- Verifica que se ha respondido al objetivo y que no faltan datos esenciales.
- Comprueba que las afirmaciones importantes se pueden justificar o se presentan
  claramente como supuestos.

## Límites

- No inventes datos, requisitos ni recursos.
- Explica cualquier incertidumbre relevante.
- Protege información sensible y respeta las instrucciones del usuario.

## Resultado

Entrega una respuesta práctica, estructurada y fácil de revisar."""
    return SkillBuilderEnvelope(
        assistant_message=(
            "No se pudo completar el diseño especializado, así que he preparado "
            "un borrador editable con tu petición para que no pierdas el trabajo."
        ),
        status="ready",
        draft=SkillDraft(
            name=name,
            description=f"Capacidad reutilizable para {cleaned or 'la tarea solicitada'}.",
            content=content,
        ),
    )
