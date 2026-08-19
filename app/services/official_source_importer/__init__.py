"""Descarga segura y determinista del contenido de una fuente oficial.

Lo detectado aquí no se persiste: se lo lleva `official_source_sync` para
materializarlo como recursos normales.

Partido en paquete porque el módulo único llegó a 1081 líneas encadenando cinco
pasos que solo comparten los límites:

    _shared.py     límites, catálogos y errores.
    download.py    traer el repositorio sin fiarse de él (incluido el SSRF).
    content.py     leer un fichero: frontmatter, mapas, qué es un agente.
    references.py  qué componente menciona a cuál.
    detection.py   qué componente es cada fichero.
    validation.py  qué se puede importar y qué hay que avisar.
    importer.py    la clase que encadena los pasos.
"""

from __future__ import annotations

from app.services.official_source_importer._shared import (
    GitHubImportError,
    OfficialRepositoryImportError,
)
from app.services.official_source_importer.detection import detect_components
from app.services.official_source_importer.download import (
    parse_github_repository,
    parse_repository_url,
)
from app.services.official_source_importer.importer import OfficialSourceImporter
from app.services.official_source_importer.validation import (
    unique_import_notices,
    validate_components,
)

__all__ = [
    "OfficialSourceImporter",
    "OfficialRepositoryImportError",
    "GitHubImportError",
    "parse_repository_url",
    "parse_github_repository",
    "detect_components",
    "validate_components",
    "unique_import_notices",
]
