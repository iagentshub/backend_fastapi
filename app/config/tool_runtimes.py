"""Canonical Tool runtime catalog and legacy compatibility helpers.

The API still accepts ``python``, ``shell`` and ``cpp``.  ``cpp`` is retained
as a wire value only: a compiled artifact is a native executable, regardless
of the language used to build it.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

TOOL_TARGET_OSES: tuple[str, ...] = ("linux", "macos", "windows")
TOOL_TARGET_ARCHITECTURES: tuple[str, ...] = ("x64", "arm64")

TOOL_RUNTIME_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "api_value": "python",
        "kind": "script",
        "translation_key": "tools.language_python",
        "extensions": (".py",),
        "requires_artifact": False,
    },
    {
        "api_value": "shell",
        "kind": "script",
        "translation_key": "tools.language_shell",
        "extensions": (".sh",),
        "requires_artifact": False,
    },
    {
        "api_value": "cpp",
        "kind": "native_binary",
        "translation_key": "tools.language_cpp",
        "extensions": (".cpp",),
        "requires_artifact": True,
        "target_operating_systems": TOOL_TARGET_OSES,
        "target_architectures": TOOL_TARGET_ARCHITECTURES,
    },
)

TOOL_RUNTIMES = frozenset(item["api_value"] for item in TOOL_RUNTIME_CATALOG)
TOOL_RUNTIME_BY_VALUE = {str(item["api_value"]): item for item in TOOL_RUNTIME_CATALOG}
TOOL_RUNTIME_BY_EXTENSION = {
    extension: str(item["api_value"])
    for item in TOOL_RUNTIME_CATALOG
    for extension in item["extensions"]
}


def infer_tool_runtime(path: str) -> str:
    return TOOL_RUNTIME_BY_EXTENSION.get(PurePosixPath(path).suffix.lower(), "")


def public_tool_runtime_catalog() -> list[dict[str, Any]]:
    """JSON-safe catalog exposed to clients; tuples become ordinary lists."""
    return [
        {
            **item,
            "extensions": list(item["extensions"]),
        }
        for item in TOOL_RUNTIME_CATALOG
    ]
