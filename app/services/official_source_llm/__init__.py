"""Análisis estructurado de repositorios oficiales mediante una conexión LLM.

El modelo solo propone un manifiesto. Las rutas, contenidos, hashes, tipos y
relaciones se vuelven a validar contra el snapshot fijado a commit; nunca se
ejecuta contenido del repositorio ni se confía en texto generado como recurso.

Partido en paquete porque el módulo único llegó a 710 líneas, de las que la
clase era poco más de la mitad: el resto eran los modelos de la respuesta, el
filtrado y troceado del repositorio, y los prompts.

    models.py    lo que el LLM tiene que devolver.
    _filters.py  qué se le enseña y en qué trozos.
    prompts.py   lo que se le pide y cómo se lee la respuesta.
    analyzer.py  la clase.
"""

from __future__ import annotations

from app.services.official_source_llm.analyzer import OfficialSourceLLMAnalyzer
from app.services.official_source_llm.models import (
    LLMManifestComponent,
    LLMManifestRelation,
    LLMRelationType,
    LLMRepositoryManifest,
    LLMResourceType,
    ProgressCallback,
)

__all__ = [
    "OfficialSourceLLMAnalyzer",
    "LLMRepositoryManifest",
    "LLMManifestComponent",
    "LLMManifestRelation",
    "LLMResourceType",
    "LLMRelationType",
    "ProgressCallback",
]
