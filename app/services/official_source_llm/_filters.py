"""Qué ficheros del repositorio se le enseñan al LLM, y en qué trozos.

Un repositorio entero no cabe en una ventana de contexto, así que se filtra por
relevancia y se parte en paquetes. Los tres límites de troceado viven aquí, con
el código que los aplica: `tests/services/test_official_source_import.py`
parchea `_CHUNK_FILES` en este módulo para forzar varios trozos.
"""


from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Dict, List

from app.models.official_source import PackageComponent

_CHUNK_CHARS = 180_000

_CHUNK_FILES = 60

_MAX_CHUNKS = 256

_LLM_RESOURCE_DIRECTORIES = frozenset(
    {
        "agents",
        "skills",
        "commands",
        "prompts",
        "knowledge",
        "documents",
        "hooks",
        "memory",
        "mcp",
        "mcp-configs",
        "rules",
        "tools",
        "workflows",
        "plugins",
        ".agents",
        ".claude",
        ".claude-plugin",
        ".codex",
        ".cursor",
        ".gemini",
        ".kiro",
        ".openclaw",
        ".opencode",
    }
)

_LLM_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".github",
        "__fixtures__",
        "benchmarks",
        "build",
        "coverage",
        "dist",
        "docs",
        "documentation",
        "evals",
        "examples",
        "fixtures",
        "node_modules",
        "test",
        "testdata",
        "tests",
        "vendor",
    }
)

_LLM_METADATA_FILES = frozenset(
    {
        "iagentshub.json",
        "manifest.json",
        "marketplace.json",
        "plugin.json",
        "plugin.yaml",
        "plugin.yml",
    }
)

_LLM_IGNORED_FILE_MARKERS = (
    ".expected.",
    ".fixture.",
    ".generated.",
    ".golden.",
    ".input.",
    ".spec.",
    ".test.",
)

def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "component"

def _pack_files(files: Dict[str, str]) -> List[List[tuple[str, str]]]:
    packets: List[List[tuple[str, str]]] = []
    current: List[tuple[str, str]] = []
    current_chars = 0
    for path, content in sorted(files.items()):
        size = len(path) + len(content) + 80
        if current and (
            current_chars + size > _CHUNK_CHARS or len(current) >= _CHUNK_FILES
        ):
            packets.append(current)
            current = []
            current_chars = 0
        current.append((path, content))
        current_chars += size
    if current:
        packets.append(current)
    if len(packets) > _MAX_CHUNKS:
        raise ValueError(
            "El repositorio requiere demasiados fragmentos para analizarlo con LLM"
        )
    return packets

def _llm_relevant_files(
    files: Dict[str, str], deterministic: List[PackageComponent]
) -> Dict[str, str]:
    """Reduce el contexto a definiciones plausibles, nunca corpus de pruebas."""
    declared_paths = {component.source_path for component in deterministic}
    result: Dict[str, str] = {}
    for path, content in files.items():
        pure = PurePosixPath(path)
        parts = tuple(part.lower() for part in pure.parts)
        name = pure.name.lower()
        if any(part in _LLM_IGNORED_PARTS for part in parts):
            continue
        if any(marker in name for marker in _LLM_IGNORED_FILE_MARKERS):
            continue
        relevant_directory = any(
            part in _LLM_RESOURCE_DIRECTORIES for part in parts[:-1]
        )
        if (
            path in declared_paths
            or relevant_directory
            or name in _LLM_METADATA_FILES
        ):
            result[path] = content
    return result

def _llm_path_priority(path: str) -> tuple[int, str]:
    root = PurePosixPath(path).parts[0].lower()
    if root in {
        "agents",
        "commands",
        "documents",
        "knowledge",
        "memory",
        "prompts",
        "skills",
        "tools",
        "workflows",
    }:
        return 0, path
    if root == "plugins":
        return 40, path
    if root.startswith("."):
        return 30, path
    return 20, path
