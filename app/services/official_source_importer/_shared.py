"""Límites, catálogos y errores del importador.

Los límites son la defensa contra un repositorio hostil: un JSON enorme, un
archivo que se descomprime hasta llenar el disco o un árbol con cien mil
ficheros. Van juntos y en un solo sitio porque se revisan juntos.

Son **defensa del proceso**, no política de producto, y por eso ninguno es
configurable desde el panel: lo que un administrador puede subir es el tamaño de
una petición (`max_request_bytes`), no cuánto está dispuesto este proceso a
descomprimir en memoria. Distinguir las dos clases es lo que evita que la
siguiente cifra se escriba sin saber a cuál pertenece.
"""


from __future__ import annotations

import re

_MAX_JSON_BYTES = 2 * 1024 * 1024

_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

_MAX_UNPACKED_BYTES = 500 * 1024 * 1024

_MAX_IMPORTED_TEXT_BYTES = 60 * 1024 * 1024

_MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024

_MAX_FILES = 4000

_ALLOWED_LICENSES = frozenset(
    {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0"}
)

_TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".txt",
        ".js",
        ".mjs",
        ".ts",
        ".py",
        ".sh",
        ".ps1",
    }
)

_DANGEROUS_PATTERNS = (
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "borrado recursivo"),
    (
        re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sh|bash)\b", re.IGNORECASE),
        "descarga ejecutada por shell",
    ),
    (
        re.compile(r"\b(Invoke-Expression|IEX)\b", re.IGNORECASE),
        "ejecución dinámica de PowerShell",
    ),
    (
        re.compile(r"\b(child_process\.(exec|spawn)|subprocess\.)", re.IGNORECASE),
        "creación de procesos",
    ),
)

_ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {"api.github.com", "codeload.github.com", "gitlab.com"}
)

class OfficialRepositoryImportError(ValueError):
    pass

GitHubImportError = OfficialRepositoryImportError

def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "component"
