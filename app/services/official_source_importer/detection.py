"""Qué hay en el repositorio: de cada fichero, qué componente es.

La clasificación va por carpeta raíz y extensión; `_IGNORED_ROOTS` evita
importar `.git`, `node_modules` u otras carpetas ajenas al paquete. `.github`
sí se inspecciona porque puede contener agentes de GitHub Copilot.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.config.content_languages import language_label
from app.config.tool_runtimes import infer_tool_runtime
from app.models.official_source import COMPONENT_TYPES, PackageComponent
from app.services.official_source_importer._shared import (
    _DANGEROUS_PATTERNS,
    _slug,
)
from app.services.official_source_importer.content import (
    _frontmatter,
    _is_agent_definition,
)
from app.services.official_source_importer.references import (
    _resolve_component_relations,
)
from app.storage.skill_storage import ensure_origin_label

_ROOT_KINDS = {
    "agents": "agent",
    "skills": "skill",
    "commands": "command",
    "prompts": "prompt",
    "knowledge": "knowledge",
    "documents": "knowledge",
    "memory": "memory",
    "tools": "tool",
    "workflows": "workflow",
    "rules": "rule",
    "hooks": "hook",
    "mcp": "mcp",
    "mcp-configs": "mcp",
}

_PLATFORM_ROOTS = frozenset(
    {
        ".agents",
        ".claude",
        ".codex",
        ".openclaw",
        ".opencode",
        ".cursor",
        ".kiro",
        ".gemini",
        ".github",
    }
)

_IGNORED_ROOTS = frozenset(
    {
        ".git",
        "benchmarks",
        "docs",
        "documentation",
        "evals",
        "examples",
        "node_modules",
        "tests",
        "test",
        "vendor",
        "dist",
    }
)

_KIND_EXTENSIONS = {
    "agent": {".md", ".json", ".yaml", ".yml", ".toml"},
    "skill": {".md"},
    "command": {".md", ".toml"},
    "prompt": {".md", ".txt"},
    "knowledge": {".md", ".txt", ".json", ".yaml", ".yml", ".toml"},
    "memory": {".md", ".txt", ".json", ".yaml", ".yml", ".toml"},
    "tool": {".py", ".sh", ".ps1", ".js", ".mjs", ".ts"},
    "workflow": {".json", ".yaml", ".yml"},
    "rule": {".md", ".txt"},
    "hook": {".json", ".yaml", ".yml", ".py", ".sh", ".ps1", ".js", ".ts"},
    "mcp": {".json", ".yaml", ".yml", ".toml"},
}


def _component_location(
    path: str, *, declared: bool = False
) -> Optional[Tuple[str, int]]:
    """Tipo y prioridad. Las raíces del repositorio ganan a sus adaptadores."""
    pure = PurePosixPath(path)
    lowered = tuple(part.lower() for part in pure.parts)
    if not lowered or lowered[0] in _IGNORED_ROOTS:
        return None
    if declared:
        return "unknown", -100
    root = lowered[0]
    kind = _ROOT_KINDS.get(root)
    priority = 0
    if kind is None and root in _PLATFORM_ROOTS:
        nested = next((part for part in lowered[1:] if part in _ROOT_KINDS), None)
        if nested not in {"agents", "skills", "commands", "prompts"}:
            return None
        kind = _ROOT_KINDS[nested]
        priority = 30
    elif kind is None and root == "plugins" and len(lowered) >= 3:
        nested = lowered[2]
        if nested not in {"agents", "skills", "commands", "prompts"}:
            return None
        kind = _ROOT_KINDS[nested]
        priority = 40
    elif kind is None and root == "src" and len(lowered) >= 2:
        nested = lowered[1]
        if nested not in {"tools", "hooks", "rules", "mcp", "mcp-configs"}:
            return None
        kind = _ROOT_KINDS[nested]
        priority = 20
    if kind is None or pure.suffix.lower() not in _KIND_EXTENSIONS[kind]:
        return None
    if kind == "skill" and pure.name.upper() != "SKILL.MD":
        return None
    if kind in {"agent", "command", "prompt"} and len(pure.parts) > 4:
        return None
    if pure.suffix.lower() == ".toml":
        priority += 5
    return kind, priority


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _component_dependencies(
    meta: Dict[str, Any], declared: Dict[str, Any]
) -> List[str]:
    values = _string_list(meta.get("dependencies"))
    # Skills, documentos, prompts y memoria no son permisos de ejecución y se
    # pueden interpretar en frontmatter común. "tools", en cambio, suele ser
    # la lista Read/Write/Bash de Claude/Codex: solo un manifiesto nativo puede
    # convertir esa clave en relaciones de recursos.
    for key in ("skills", "knowledge", "prompts", "memory", "workflows"):
        values.extend(_string_list(meta.get(key)))
    for key in ("skills", "knowledge", "prompts", "tools", "memory", "workflows"):
        values.extend(_string_list(declared.get(key)))
    resources = meta.get("resources")
    if isinstance(resources, dict):
        for value in resources.values():
            values.extend(_string_list(value))
    relations = declared.get("relations")
    if isinstance(relations, dict):
        for value in relations.values():
            values.extend(_string_list(value))
    return list(dict.fromkeys(values))


def _manifest_components(files: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    for path in (
        ".iagentshub/manifest.json",
        "iagentshub.json",
        "plugin.json",
        "plugin.yaml",
        "plugin.yml",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
        raw = files.get(path)
        if not raw:
            continue
        try:
            manifest = (
                json.loads(raw)
                if PurePosixPath(path).suffix == ".json"
                else yaml.safe_load(raw)
            )
        except (json.JSONDecodeError, yaml.YAMLError):
            continue
        declared = manifest.get("components") if isinstance(manifest, dict) else None
        if not isinstance(declared, list):
            return {}
        return {
            str(item.get("source_path") or ""): item
            for item in declared
            if isinstance(item, dict) and item.get("source_path")
        }
    return {}


def detect_components(source_id: str, files: Dict[str, str]) -> List[PackageComponent]:
    candidates: List[Tuple[int, PackageComponent]] = []
    declared_components = _manifest_components(files)
    canonical_kinds = {
        _ROOT_KINDS[PurePosixPath(path).parts[0].lower()]
        for path in files
        if PurePosixPath(path).parts
        and PurePosixPath(path).parts[0].lower() in _ROOT_KINDS
    }
    if "command" in canonical_kinds:
        canonical_kinds.add("prompt")
    for path, content in sorted(files.items()):
        declared = declared_components.get(path, {})
        declared_type = str(
            declared.get("type") or declared.get("component_type") or ""
        )
        location = _component_location(path, declared=bool(declared))
        if not location:
            continue
        inferred_kind, priority = location
        kind = declared_type if declared_type in COMPONENT_TYPES else inferred_kind
        if kind == "unknown" and not declared_type:
            continue
        workflow_dependencies: List[str] = []
        if kind == "workflow":
            try:
                definition = yaml.safe_load(content) or {}
            except yaml.YAMLError:
                definition = {}
            if not isinstance(definition, dict) or not {
                "nodes",
                "edges",
            }.issubset(definition):
                continue
            workflow_dependencies = [
                str(node.get("agent_id") or "")
                for node in definition.get("nodes", [])
                if isinstance(node, dict) and node.get("agent_id")
            ]
        pure = PurePosixPath(path)
        meta = {**_frontmatter(content), **declared}
        detected_by = "native_manifest" if declared else "canonical_directory"
        if kind == "agent" and not _is_agent_definition(
            pure, content, meta, declared=bool(declared)
        ):
            kind = "unknown"
            detected_by = "ambiguous_agent_file"
        if kind == "tool" and not declared:
            kind = "unknown"
            detected_by = "undeclared_executable"
        inferred = pure.parent.name if pure.name.upper() == "SKILL.MD" else pure.stem
        name = str(
            meta.get("name") or inferred.replace("-", " ").replace("_", " ").title()
        )
        component_id = _slug(str(meta.get("id") or inferred))
        companion_files: Dict[str, str] = {}
        if kind == "skill":
            prefix = pure.parent.as_posix().rstrip("/") + "/"
            companion_files = {
                candidate[len(prefix) :]: body
                for candidate, body in files.items()
                if candidate.startswith(prefix) and candidate != path
            }
        digest_source = content + "".join(
            key + companion_files[key] for key in sorted(companion_files)
        )
        language = str(meta.get("language") or meta.get("lang") or "").lower()
        content_language = language_label(language) or ""
        labels = ensure_origin_label(_string_list(meta.get("labels")), "official")
        if content_language:
            labels.append(content_language)
        tool_language = infer_tool_runtime(path)
        executable_candidate = (
            kind in {"tool", "hook"} or detected_by == "undeclared_executable"
        )
        executable = kind == "tool"
        blocked = executable_candidate and (
            (kind == "tool" and not infer_tool_runtime(path))
            or any(
                pattern.search(content) and label == "borrado recursivo"
                for pattern, label in _DANGEROUS_PATTERNS
            )
        )
        component = PackageComponent(
            source_id=source_id,
            component_id=component_id,
            component_type=kind,
            name=name,
            description=str(meta.get("description") or ""),
            source_path=path,
            content=content,
            files=companion_files,
            labels=list(dict.fromkeys(labels)),
            dependencies=list(
                dict.fromkeys(
                    [*_component_dependencies(meta, declared), *workflow_dependencies]
                )
            ),
            content_hash=hashlib.sha256(digest_source.encode()).hexdigest(),
            language=content_language,
            tool_language=tool_language if kind == "tool" else "",
            detected_by=detected_by,
            executable=executable,
            security_blocked=blocked,
            security_review_required=executable_candidate,
        )
        candidates.append((priority, component))

    # Una plataforma puede publicar la misma pieza para varios clientes. La
    # raíz canónica produce un objeto; el resto queda registrado como variante.
    grouped: Dict[Tuple[str, str], List[Tuple[int, PackageComponent]]] = {}
    for candidate in candidates:
        component = candidate[1]
        grouped.setdefault(
            (component.component_type, component.component_id), []
        ).append(candidate)
    components: List[PackageComponent] = []
    used_ids: set[str] = set()
    for (kind, base_id), variants in sorted(grouped.items()):
        variants.sort(key=lambda item: (item[0], item[1].source_path))
        if variants[0][0] >= 30 and kind in canonical_kinds:
            continue
        component = variants[0][1]
        component.variants = [item[1].source_path for item in variants[1:]]
        component_id = base_id
        number = 2
        while component_id in used_ids:
            component_id = f"{base_id}-{number}"
            number += 1
        component.component_id = component_id
        used_ids.add(component_id)
        components.append(component)
    components.sort(key=lambda item: (item.component_type, item.component_id))
    _resolve_component_relations(components)
    return components
