"""El catálogo de avisos no puede quedarse atrás del código que los emite.

Un tipo nuevo se añade en tres sitios —el productor, la plantilla de correo y
el catálogo— y el que se olvida es siempre el tercero. El fallo resultante no
se ve: el aviso llega igual, pero ignorando las preferencias del usuario, que
es la clase de cosa que nadie reporta porque parece que funciona.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.models.notification_kinds import (
    CATEGORIA_POR_DEFECTO,
    CATEGORIAS,
    categoria_de,
    categorias_publicas,
)

RAIZ = Path(__file__).resolve().parents[2]


def _kinds_con_plantilla_de_correo() -> set[str]:
    """Los `notif_<kind>` declarados en `_TEXTOS` de `app/services/email.py`."""
    fuente = (RAIZ / "app/services/email.py").read_text(encoding="utf-8")
    return set(re.findall(r'"notif_([a-z_]+)":\s*\{', fuente))


def _kinds_emitidos() -> set[str]:
    """Los `kind=` que pasan los productores a `notify(...)`."""
    emitidos: set[str] = set()
    for fichero in (RAIZ / "app/api/routes").rglob("*.py"):
        emitidos |= set(
            re.findall(r'kind="([a-z_]+)"', fichero.read_text(encoding="utf-8"))
        )
    return emitidos


def test_todo_tipo_con_correo_esta_catalogado():
    huerfanos = {
        kind
        for kind in _kinds_con_plantilla_de_correo()
        if categoria_de(kind) == CATEGORIA_POR_DEFECTO
    }
    assert huerfanos == set(), (
        "Estos tipos tienen plantilla de correo pero ninguna categoría, así que "
        "el usuario no puede apagarlos por separado:\n"
        f"{sorted(huerfanos)}\n"
        "Añádelos a CATEGORIAS en app/models/notification_kinds.py."
    )


def test_todo_tipo_emitido_esta_catalogado():
    huerfanos = {
        kind
        for kind in _kinds_emitidos()
        if categoria_de(kind) == CATEGORIA_POR_DEFECTO
    }
    assert huerfanos == set(), (
        "Estos tipos se emiten desde una ruta y no están catalogados:\n"
        f"{sorted(huerfanos)}"
    )


def test_todo_tipo_catalogado_tiene_su_correo():
    """Al revés: una categoría que nombra un tipo inexistente es letra muerta."""
    con_plantilla = _kinds_con_plantilla_de_correo()
    catalogados = {kind for kinds in CATEGORIAS.values() for kind in kinds}
    sobran = catalogados - con_plantilla
    assert sobran == set(), (
        f"Catalogados pero sin plantilla de correo: {sorted(sobran)}"
    )


def test_ningun_tipo_cae_en_dos_categorias():
    vistos: dict[str, str] = {}
    for categoria, kinds in CATEGORIAS.items():
        for kind in kinds:
            assert kind not in vistos, (
                f"{kind!r} está en {vistos[kind]!r} y en {categoria!r}: "
                "apagar una categoría dejaría el aviso encendido por la otra."
            )
            vistos[kind] = categoria


def test_la_categoria_por_defecto_se_ofrece_al_usuario():
    """Un tipo sin clasificar tiene que seguir siendo apagable.

    Es la red bajo el olvido: mientras alguien no lo catalogue, cae en
    `general` y el usuario puede silenciarlo igual.
    """
    assert CATEGORIA_POR_DEFECTO in categorias_publicas()
    assert categoria_de("un_tipo_que_no_existe") == CATEGORIA_POR_DEFECTO
