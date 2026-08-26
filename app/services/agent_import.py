"""Parse untrusted local agent files into a safe, non-persistent draft."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

import yaml

from app.errors import APIError
from app.models.agent_import import (
    AgentImportDraft,
    AgentImportIssue,
    AgentImportPreview,
    AgentImportReference,
)
from app.services.agent_import_types import AGENT_FIELD_TO_RESOURCE_KIND

_ALLOWED_EXTENSIONS = {".md", ".json"}
_AGENT_TYPES = {"generic", "claude", "openai", "github"}
_TYPE_ALIASES = {
    "anthropic": "claude",
    "claude-code": "claude",
    "copilot": "github",
    "github-copilot": "github",
    "assistant": "generic",
}
_RESOURCE_FIELDS = tuple(AGENT_FIELD_TO_RESOURCE_KIND)
_SAFE_FIELDS = {
    "name",
    "title",
    "description",
    "agent_type",
    "type",
    "model",
    "system_prompt",
    "instructions",
    "prompt",
    "temperature",
    "agent",
    *_RESOURCE_FIELDS,
}
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def safe_import_filename(raw: str | None) -> str:
    """Return only a local basename, never a client-side absolute path."""

    normalized = str(raw or "agent.md").replace("\\", "/")
    return PurePosixPath(normalized).name or "agent.md"


def _invalid_file(message: str, *, reason: str) -> APIError:
    return APIError(
        422,
        "invalid_field",
        message,
        extra={"field": "file", "reason": reason},
    )


def _string(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _resource_names(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates: list[Any] = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        candidates = value
    else:
        return []

    names: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str):
            name = candidate.strip()
        elif isinstance(candidate, Mapping):
            name = _string(candidate, "name", "id")
        else:
            continue
        if name and name not in names:
            names.append(name)
    return names


def _agent_type(
    source: Mapping[str, Any],
    *,
    filename: str,
    is_json: bool,
    issues: list[AgentImportIssue],
) -> str:
    raw = _string(source, "agent_type", "type").lower().replace("_", "-")
    raw = _TYPE_ALIASES.get(raw, raw)
    if raw in _AGENT_TYPES:
        return raw
    if raw:
        issues.append(AgentImportIssue(code="unknown_agent_type", field="agent_type"))

    lower_name = filename.lower()
    # Both ecosystems use arbitrary file names, so their distinctive
    # frontmatter keys are a stronger hint than the extension.
    if any(key in source for key in ("permissionMode", "disallowedTools", "hooks")):
        return "claude"
    if any(key in source for key in ("target", "mcp-servers", "handoffs")):
        return "github"
    if "claude" in lower_name:
        return "claude"
    if "copilot" in lower_name or "github" in lower_name:
        return "github"
    if is_json and isinstance(source.get("instructions"), str):
        return "openai"
    return "generic"


def _temperature(source: Mapping[str, Any], issues: list[AgentImportIssue]) -> float:
    raw = source.get("temperature")
    if raw is None:
        return 0.7
    try:
        value = float(raw)
    except (TypeError, ValueError):
        issues.append(AgentImportIssue(code="invalid_temperature", field="temperature"))
        return 0.7
    if not 0.0 <= value <= 1.0:
        issues.append(AgentImportIssue(code="invalid_temperature", field="temperature"))
        return 0.7
    return value


def _build_preview(
    *,
    filename: str,
    source: Mapping[str, Any],
    prompt: str,
    is_json: bool,
) -> AgentImportPreview:
    issues: list[AgentImportIssue] = []
    agent_type = _agent_type(
        source,
        filename=filename,
        is_json=is_json,
        issues=issues,
    )
    fallback_name = re.sub(r"(?:\.agent)?\.(?:md|json)$", "", filename, flags=re.I)
    name = _string(source, "name", "title") or fallback_name or "Agente importado"
    if not _string(source, "name", "title"):
        issues.append(AgentImportIssue(code="name_from_filename", field="name"))

    references = [
        AgentImportReference(
            key=f"{field}:{index}",
            kind=AGENT_FIELD_TO_RESOURCE_KIND[field],
            source=name,
        )
        for field in _RESOURCE_FIELDS
        for index, name in enumerate(_resource_names(source.get(field)))
    ]
    if references:
        issues.append(
            AgentImportIssue(
                code="resource_references_found",
                values=sorted({reference.kind for reference in references}),
            )
        )

    ignored = sorted(str(key) for key in source if str(key) not in _SAFE_FIELDS)
    if ignored:
        issues.append(AgentImportIssue(code="fields_ignored", values=ignored))

    source_format: str
    if is_json:
        source_format = "openai_json" if agent_type == "openai" else "agent_json"
    elif agent_type == "claude":
        source_format = "claude_markdown"
    elif agent_type == "github":
        source_format = "github_markdown"
    else:
        source_format = "markdown"

    return AgentImportPreview(
        filename=filename,
        source_format=source_format,  # type: ignore[arg-type]
        draft=AgentImportDraft(
            name=name[:200],
            description=_string(source, "description")[:2000],
            agent_type=agent_type,  # type: ignore[arg-type]
            model=_string(source, "model")[:200],
            system_prompt=prompt.strip(),
            temperature=_temperature(source, issues),
        ),
        references=references,
        issues=issues,
        ignored_fields=ignored,
    )


def parse_agent_import(filename: str, content: bytes) -> AgentImportPreview:
    """Parse bytes without persisting or trusting ownership-related metadata."""

    filename = safe_import_filename(filename)
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise _invalid_file(
            "Solo se admiten archivos .md o .json",
            reason="unsupported_extension",
        )
    if not content:
        raise _invalid_file("El archivo está vacío", reason="empty")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _invalid_file(
            "El archivo no contiene texto UTF-8 válido",
            reason="invalid_encoding",
        ) from None
    if "\x00" in text:
        raise _invalid_file(
            "El archivo no contiene texto válido",
            reason="binary_content",
        )

    if extension == ".json":
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            raise _invalid_file("El JSON no es válido", reason="invalid_json") from None
        if not isinstance(decoded, Mapping):
            raise _invalid_file(
                "El JSON debe contener un objeto de agente",
                reason="invalid_json_shape",
            )
        nested = decoded.get("agent")
        source = nested if isinstance(nested, Mapping) else decoded
        prompt = _string(source, "system_prompt", "instructions", "prompt")
        return _build_preview(
            filename=filename,
            source=source,
            prompt=prompt,
            is_json=True,
        )

    metadata: Mapping[str, Any] = {}
    body = text
    if text.startswith("---"):
        match = _FRONTMATTER.match(text)
        if match is None:
            raise _invalid_file(
                "El frontmatter Markdown no está cerrado correctamente",
                reason="invalid_frontmatter",
            )
        try:
            loaded = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            raise _invalid_file(
                "El frontmatter YAML no es válido",
                reason="invalid_frontmatter",
            ) from None
        if not isinstance(loaded, Mapping):
            raise _invalid_file(
                "El frontmatter debe contener campos de agente",
                reason="invalid_frontmatter_shape",
            )
        metadata = loaded
        body = text[match.end() :]
    prompt = body.strip() or _string(
        metadata, "system_prompt", "instructions", "prompt"
    )
    return _build_preview(
        filename=filename,
        source=metadata,
        prompt=prompt,
        is_json=False,
    )
