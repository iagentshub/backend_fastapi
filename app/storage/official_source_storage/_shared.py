"""Las tablas que puede traer una fuente y el error de idioma heredado."""


from __future__ import annotations

import re
from typing import Any, Dict

from app.config.content_languages import CONTENT_LANGUAGE_LABELS

# Tablas de recurso que pueden llevar contenido oficial, con su tipo lógico.


# Tablas de recurso que pueden llevar contenido oficial, con su tipo lógico.
OFFICIAL_RESOURCE_TABLES: Dict[str, str] = {
    "agent": "agents",
    "skill": "skills",
    "prompt": "prompts",
    "tool": "tools",
    "knowledge": "knowledge_items",
    "workflow": "agent_workflows",
}

SOURCE_RESOURCE_TYPES = frozenset({*OFFICIAL_RESOURCE_TABLES, "memory"})

_INVALID_LANGUAGE_ERROR = re.compile(
    r"^[^:]+: etiquetas no válidas \(([^)]+)\)$"
)

def _is_legacy_invalid_language_error(value: Any) -> bool:
    match = _INVALID_LANGUAGE_ERROR.fullmatch(str(value))
    if not match:
        return False
    labels = {item.strip() for item in match.group(1).split(",")}
    return bool(labels) and all(
        label.startswith("lang_") and label not in CONTENT_LANGUAGE_LABELS
        for label in labels
    )
