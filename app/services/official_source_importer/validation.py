"""Qué se puede importar y qué hay que avisar antes de hacerlo."""


from __future__ import annotations

import json
import posixpath
import re
from pathlib import PurePosixPath
from typing import Any, Iterable, List, Tuple

from app.models.official_source import PackageComponent
from app.services.official_source_importer._shared import (
    _DANGEROUS_PATTERNS,
)
from app.storage.skill_storage import SKILL_LABELS


def validate_components(
    components: List[PackageComponent],
) -> Tuple[List[str], List[Any]]:
    """Devuelve errores bloqueantes y avisos de seguridad para la revisión."""
    errors: List[str] = []
    warnings: List[Any] = []
    component_ids = {component.component_id for component in components}
    dependencies_by_id = {
        component.component_id: component.dependencies for component in components
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> bool:
        if component_id in visiting:
            return True
        if component_id in visited:
            return False
        visiting.add(component_id)
        cyclic = any(
            dependency in dependencies_by_id and visit(dependency)
            for dependency in dependencies_by_id.get(component_id, [])
        )
        visiting.remove(component_id)
        visited.add(component_id)
        return cyclic

    if any(visit(component_id) for component_id in sorted(component_ids)):
        errors.append("El grafo de dependencias contiene un ciclo")
    markdown_link = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for component in components:
        invalid_labels = [
            label for label in component.labels if label not in SKILL_LABELS
        ]
        if invalid_labels:
            errors.append(
                f"{component.component_id}: etiquetas no válidas ({', '.join(invalid_labels)})"
            )
        missing_dependencies = [
            item for item in component.dependencies if item not in component_ids
        ]
        if missing_dependencies:
            errors.append(
                f"{component.component_id}: dependencias no encontradas "
                f"({', '.join(missing_dependencies)})"
            )
        missing_relations = [
            str(relation.get("target_id") or "")
            for relation in component.relations
            if str(relation.get("target_id") or "") not in component_ids
        ]
        if missing_relations:
            errors.append(
                f"{component.component_id}: relaciones no encontradas "
                f"({', '.join(missing_relations)})"
            )
        component_root = PurePosixPath(component.source_path).parent
        texts = {component.source_path: component.content}
        texts.update(
            {
                component_root.joinpath(relative).as_posix(): content
                for relative, content in component.files.items()
            }
        )
        for path, content in texts.items():
            for match in markdown_link.finditer(content):
                destination = match.group(1).strip().split("#", 1)[0]
                if (
                    not destination
                    or "://" in destination
                    or destination.startswith(("#", "mailto:"))
                ):
                    continue
                resolved = posixpath.normpath(
                    PurePosixPath(path).parent.joinpath(destination).as_posix()
                )
                if (
                    resolved == ".."
                    or resolved.startswith("../")
                    or resolved.startswith("/")
                ):
                    warnings.append(
                        {
                            "level": "log",
                            "code": "external_markdown_reference",
                            "message": (
                                f"{component.component_id}: referencia fuera del "
                                f"repositorio ({destination})"
                            ),
                        }
                    )
            for pattern, label in _DANGEROUS_PATTERNS:
                if pattern.search(content):
                    warnings.append(
                        f"{component.component_id}: posible {label} en {path}"
                    )
    return sorted(set(errors)), unique_import_notices(warnings)

def unique_import_notices(values: Iterable[Any]) -> List[Any]:
    """Deduplica avisos conservando mensajes estructurados para la UI."""
    result: List[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
