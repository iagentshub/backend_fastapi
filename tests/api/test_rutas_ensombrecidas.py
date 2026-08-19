"""Ninguna ruta paramétrica se traga a una literal registrada después.

FastAPI resuelve por orden de registro. `PUT /api/knowledge/{item_id}` casa con
`/api/knowledge/packs` y, si se registra antes, el handler de packs deja de
recibir peticiones — sin error, sin test rojo, con `item_id="packs"`.

Esto no lo cubre `contrato_rutas.txt`, que congela el CONJUNTO de rutas y lo
ordena alfabéticamente: el orden de registro no aparece ahí. Al partir un
módulo en paquete el orden lo pasa a decidir isort sobre los imports del
`__init__`, así que conviene que sea un test y no una convención.

Un parámetro de path no cruza `/`: solo colisionan rutas con el mismo número de
segmentos.
"""

from __future__ import annotations

import re


def _rutas_en_orden() -> list[tuple[str, str]]:
    from app.api.app import create_app

    esquema = create_app().openapi()
    return [
        (metodo.upper(), ruta)
        for ruta, operaciones in esquema["paths"].items()
        for metodo in operaciones
    ]


def _patron(ruta: str) -> re.Pattern[str]:
    partes = [
        r"[^/]+" if seg.startswith("{") else re.escape(seg)
        for seg in ruta.split("/")
    ]
    return re.compile("^" + "/".join(partes) + "$")


def test_ninguna_ruta_parametrica_ensombrece_a_una_literal(patch_data_dir):
    rutas = _rutas_en_orden()
    ensombrecidas = []

    for i, (metodo, ruta) in enumerate(rutas):
        if "{" not in ruta:
            continue
        patron = _patron(ruta)
        for metodo_posterior, posterior in rutas[i + 1 :]:
            if metodo_posterior != metodo or posterior == ruta:
                continue
            # Solo es un problema si la posterior es más concreta: la genérica
            # la captura antes y la posterior queda inalcanzable.
            if patron.match(posterior) and posterior.count("{") < ruta.count("{"):
                ensombrecidas.append(f"{metodo} {ruta}  tapa a  {posterior}")

    assert not ensombrecidas, (
        "Hay rutas inalcanzables: una ruta con parámetros está registrada antes\n"
        "que otra más concreta que casa con el mismo patrón.\n  "
        + "\n  ".join(ensombrecidas)
        + "\nOrdena los imports del `__init__` del paquete para que la concreta\n"
        "se registre primero."
    )
