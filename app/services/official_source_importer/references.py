"""Qué componente menciona a cuál dentro del repositorio.

Un agente cita sus skills por alias, por ruta o entre backticks, y de ahí sale
la dependencia que luego se materializa como relación entre recursos.
"""


from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Dict, List, Optional

from app.models.official_source import PackageComponent
from app.services.official_source_importer._shared import (
    _slug,
)

_REFERENCE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"((?:\.\.?/)?(?:agents|skills|commands|prompts|knowledge|documents|memory|"
    r"tools|workflows)/[A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)

_ACTIVATION_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])(?:@|\$|/)([A-Za-z0-9][A-Za-z0-9_.-]*)"
)

_BACKTICK_REFERENCE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9_.:-]*)`")

_AGENT_RESOURCE_TYPES = frozenset(
    {"skill", "knowledge", "prompt", "command", "tool", "memory"}
)

def _reference_aliases(component: PackageComponent) -> set[str]:
    path = PurePosixPath(component.source_path)
    aliases = {
        component.component_id.lower(),
        _slug(component.name),
        path.as_posix().lower(),
        path.with_suffix("").as_posix().lower(),
    }
    if path.name.upper() == "SKILL.MD":
        aliases.update(
            {
                path.parent.as_posix().lower(),
                path.parent.name.lower(),
                f"skill:{path.parent.name.lower()}",
                f"skill:{component.component_id.lower()}",
                f"skills:{component.component_id.lower()}",
            }
        )
    else:
        aliases.add(path.stem.lower())
        aliases.add(f"{component.component_type}:{path.stem.lower()}")
    prefix = path.parent.as_posix().rstrip("/")
    aliases.update(
        f"{prefix}/{relative}".lower() for relative in component.files if prefix
    )
    return {alias.strip("./") for alias in aliases if alias.strip("./")}

def _reference_candidates(value: str, source_path: str) -> List[str]:
    cleaned = value.strip().strip("`'\"<>()[]{}.,;:").split("#", 1)[0]
    cleaned = cleaned.replace("\\", "/")
    if not cleaned:
        return []
    candidates = [cleaned.lower().lstrip("./")]
    if cleaned.startswith(("./", "../")):
        relative = posixpath.normpath(
            PurePosixPath(source_path).parent.joinpath(cleaned).as_posix()
        )
        candidates.insert(0, relative.lower().lstrip("./"))
    if ":" in cleaned:
        candidates.append(cleaned.split(":", 1)[-1].lower())
    candidates.append(_slug(cleaned))
    pure = PurePosixPath(cleaned)
    if pure.name.upper() == "SKILL.MD":
        candidates.extend([pure.parent.as_posix().lower(), pure.parent.name.lower()])
    elif pure.suffix:
        candidates.append(pure.stem.lower())
    return list(dict.fromkeys(item for item in candidates if item))

def _content_references(component: PackageComponent) -> List[str]:
    values = [match.group(1) for match in _REFERENCE_PATH.finditer(component.content)]
    values.extend(
        match.group(1) for match in _ACTIVATION_REFERENCE.finditer(component.content)
    )
    values.extend(
        match.group(1) for match in _BACKTICK_REFERENCE.finditer(component.content)
    )
    return list(dict.fromkeys(values))

def _resolve_component_relations(components: List[PackageComponent]) -> None:
    """Resuelve relaciones exactas después de fijar IDs y variantes.

    Solo se automatizan referencias estructuradas (campos, rutas y tokens de
    activación). El texto libre no participa para evitar unir recursos por una
    coincidencia semántica accidental.
    """
    aliases: Dict[str, set[str]] = defaultdict(set)
    by_id = {component.component_id: component for component in components}
    for component in components:
        for alias in _reference_aliases(component):
            aliases[alias].add(component.component_id)

    def resolve(value: str, source: PackageComponent) -> Optional[str]:
        for candidate in _reference_candidates(value, source.source_path):
            matches = aliases.get(candidate, set()) - {source.component_id}
            if len(matches) == 1:
                return next(iter(matches))
        return None

    explicit_by_id = {
        component.component_id: list(component.dependencies) for component in components
    }
    for component in components:
        resolved: List[str] = []
        for reference in explicit_by_id[component.component_id]:
            target = resolve(reference, component)
            if target:
                resolved.append(target)
                continue
            # Conserva una referencia explícita no resuelta para que la
            # validación la muestre como error en vez de ocultarla.
            pure = PurePosixPath(reference)
            fallback = (
                pure.parent.name if pure.name.upper() == "SKILL.MD" else pure.stem
            )
            resolved.append(_slug(fallback or reference.split(":", 1)[-1]))
        component.dependencies = list(dict.fromkeys(resolved))

    for component in components:
        for reference in _content_references(component):
            target_id = resolve(reference, component)
            if not target_id:
                continue
            target = by_id[target_id]
            if (
                component.component_type == "agent"
                and target.component_type in _AGENT_RESOURCE_TYPES
            ):
                component.dependencies = list(
                    dict.fromkeys([*component.dependencies, target_id])
                )
                component.relations.append(
                    {"target_id": target_id, "relation_type": "uses"}
                )
            elif (
                target.component_type == "agent"
                and component.component_type in _AGENT_RESOURCE_TYPES
            ):
                component.relations.append(
                    {"target_id": target_id, "relation_type": "orchestrates"}
                )
