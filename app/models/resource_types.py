"""Registro único de tipos de recurso del sistema.

Única fuente de verdad para los valores de `resource_type`. Los routers y
storages importan de aquí en vez de mantener listas propias divergentes.
"""

from __future__ import annotations

# Recursos gestionados por el usuario (heredan de BaseResource)
RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "agent",
        "skill",
        "connection",
        "knowledge",
        "workflow",
        "llm_orchestration",
        "prompt",
        "tool",
    }
)

# Tipos con presencia social (star/publicación) — las conexiones no se publican
SOCIAL_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"agent", "skill", "knowledge", "workflow", "prompt", "tool"}
)

# Alias legacy aceptados por compatibilidad con clientes existentes
ALIASES: dict[str, str] = {
    "agents": "agent",
    "skills": "skill",
    "connections": "connection",
    "url": "knowledge",
    "document": "knowledge",
    "workflows": "workflow",
    "llm_orchestrations": "llm_orchestration",
    "prompts": "prompt",
    "tools": "tool",
}


def normalize_resource_type(raw: str) -> str:
    """Devuelve el tipo canónico; acepta alias legacy. ValueError si no existe."""
    value = ALIASES.get(raw, raw)
    if value not in RESOURCE_TYPES:
        raise ValueError(f"Tipo de recurso no válido: {raw!r}")
    return value
