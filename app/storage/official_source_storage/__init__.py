"""Persistencia de las fuentes oficiales.

Solo la fuente: lo que trae se guarda como recurso normal en agents/skills/…
con ``official_source_id`` apuntando aquí (ver services/official_source_sync).

Partido en paquete porque el módulo único llegó a 766 líneas, de las que 720
eran una clase con dos ciclos de vida distintos: la fuente y sus recursos, que
persisten, y los borradores, que caducan.

Las dos mitades son **mixins** a propósito: sus métodos se llamaban entre sí por
`self` y así ningún cuerpo cambia al moverlos.

    _shared.py   tablas que puede traer una fuente y el error de idioma heredado.
    _sources.py  la fuente y los recursos materializados.
    _drafts.py   borradores y su selección de componentes.
"""

from __future__ import annotations

from app.storage.official_source_storage._drafts import _DraftsMixin
from app.storage.official_source_storage._shared import (
    OFFICIAL_RESOURCE_TABLES,
    SOURCE_RESOURCE_TYPES,
)
from app.storage.official_source_storage._sources import _SourcesMixin

__all__ = [
    "OfficialSourceStorage",
    "OFFICIAL_RESOURCE_TABLES",
    "SOURCE_RESOURCE_TYPES",
]


class OfficialSourceStorage(_SourcesMixin, _DraftsMixin):
    """Fachada única: las dos mitades se usan siempre juntas."""
