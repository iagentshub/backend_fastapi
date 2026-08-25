"""Piezas internas compartidas por los storages de recurso de ``app/storage/``.

Aquí vive **solo** lo privado que usan dos o más módulos de recurso; lo
específico de un recurso se queda en su propio fichero, y lo que consumen los
routers (``SKILL_ASSIGNABLE_LABELS`` y ``SKILL_CATEGORIES``) vive en
el módulo público del recurso al que pertenece.

Este paquete no reexporta nada desde ``__init__.py``: los storages hacen
``from app.storage import db as _db``, así que un ``__init__`` que los
importase cerraría un ciclo módulo↔paquete y lo caza
``tests/test_ciclos_de_import.py``. Importa siempre del módulo concreto.

── Por qué cada storage importa ``db`` DOS veces ─────────────────────────────

Todos los módulos de recurso hacen a la vez::

    from app.storage import db as _db
    from app.storage.db import open_db

y no es un descuido:

- ``open_db`` es una función; da igual cuándo se resuelva el nombre, y traerla
  por valor deja el código legible.
- ``IS_PG`` es un BOOLEANO que los tests reescriben con
  ``monkeypatch.setattr(db, "IS_PG", False)``. Traerlo por valor congelaría el
  del arranque y toda la suite correría contra el dialecto equivocado — la
  trampa que documenta CLAUDE.md. Leerlo como ``_db.IS_PG`` lo consulta en cada
  llamada, que es lo único correcto aquí.

Los imports de ``db`` dentro de funciones no rompían ningún ciclo: ``db.py`` no
alcanza a estos módulos ni directa ni indirectamente. Eran costumbre, y
escondían los pocos diferidos que sí tienen motivo (ver ``DATA_DIR`` en
``connection_storage.py``).

Lo vigila ``tests/storage/test_is_pg_en_tiempo_de_llamada.py``.
"""
from __future__ import annotations

import re

from app.utils.generators import generate_id

# owner_id sintético de los recursos públicos / de sistema.
_PUBLIC_OWNER = "__public__"


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or f"item-{generate_id(8)}"
