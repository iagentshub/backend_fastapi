"""Canonical resource mappings shared by agent import parsers and directories."""

from __future__ import annotations

from app.models.agent_import import AgentImportResourceKind

AGENT_FIELD_TO_RESOURCE_KIND: dict[str, AgentImportResourceKind] = {
    "skills": "skill",
    "knowledge": "knowledge",
    "knowledge_packs": "knowledge_pack",
    "prompts": "prompt",
    "tools": "tool",
}

RESOURCE_KIND_TO_AGENT_FIELD = {
    kind: field for field, kind in AGENT_FIELD_TO_RESOURCE_KIND.items()
}

COMPONENT_TYPE_TO_RESOURCE_KIND: dict[str, AgentImportResourceKind] = {
    "skill": "skill",
    "knowledge": "knowledge",
    "prompt": "prompt",
    "command": "prompt",
    "tool": "tool",
}

SUPPORTED_DIRECTORY_COMPONENT_TYPES = frozenset(
    {"agent", *COMPONENT_TYPE_TO_RESOURCE_KIND}
)
