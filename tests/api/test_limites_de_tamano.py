"""Ningún techo de tamaño se escribe a mano dentro de una ruta.

El punto 52 dejó una sola cifra de subida y la pone el administrador
(`max_request_bytes`, aplicado por BodySizeLimitMiddleware). Lo que no se hizo
entonces fue mirar el resto del código, y había once cifras más escritas en los
ficheros que las usaban. La peor era un literal de 10 MB en la subida de un
documento de knowledge, con el número viajando al cliente dentro del error: el
mismo 10 MB que el punto 52 acababa de quitar de `upload_avatar`, escrito otra
vez en otro sitio. El patrón no se había corregido, se había movido.

Los que quedan son de otra clase y por eso siguen siendo constantes: acotan lo
que el proceso descomprime o se trae a memoria, no lo que un usuario puede
subir. Un límite que protege la memoria no debe poder subirlo el administrador,
que es justo quien lo tocaría el día que algo no cabe.

Esta guarda es lo único que evita la número doce.
"""

from __future__ import annotations

import ast
import pathlib

RUTAS = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "routes"

# 64 KB: por encima de cualquier longitud de campo que se valide a mano —los
# 2000 caracteres de una descripción, las 4500 líneas de una ejecución de
# Centinel— y muy por debajo de cualquier techo de subida plausible, que en este
# código siempre se escribe en múltiplos de MB. Un umbral más bajo marcaría esas
# longitudes, que no son de lo que trata esto, y la guarda acabaría desactivada.
UMBRAL = 64 * 1024


def _valor_de(nodo: ast.AST) -> int | None:
    """El entero de un literal, incluido `10 * 1024 * 1024`.

    A mano y no con `ast.literal_eval`, que solo evalúa `+` y `-` sobre
    complejos: con él, la forma en la que estos techos se escriben *siempre*
    —producto de mil veinticuatros— devolvía None y la guarda no veía nada.
    """
    if isinstance(nodo, ast.Constant):
        return nodo.value if isinstance(nodo.value, int) and not isinstance(nodo.value, bool) else None
    if isinstance(nodo, ast.BinOp):
        izq, der = _valor_de(nodo.left), _valor_de(nodo.right)
        if izq is None or der is None:
            return None
        if isinstance(nodo.op, ast.Mult):
            return izq * der
        if isinstance(nodo.op, ast.Add):
            return izq + der
        if isinstance(nodo.op, ast.LShift) and 0 <= der < 64:
            return izq << der
    return None


def _huele_a_tamano(texto: str) -> bool:
    minusculas = texto.lower()
    return any(
        palabra in minusculas
        for palabra in ("size", "bytes", "len(", "length", "tamano", "tamaño", "peso")
    )


def test_ninguna_comparacion_de_tamano_usa_un_literal():
    culpables = []
    for fichero in sorted(RUTAS.rglob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Compare):
                continue
            lados = [nodo.left, *nodo.comparators]
            literales = [v for lado in lados if (v := _valor_de(lado)) is not None]
            if not any(v >= UMBRAL for v in literales):
                continue
            if not any(
                _huele_a_tamano(ast.unparse(lado))
                for lado in lados
                if _valor_de(lado) is None
            ):
                continue
            culpables.append(
                f"{fichero.relative_to(RUTAS).as_posix()}:{nodo.lineno}: "
                f"{ast.unparse(nodo)}"
            )

    assert culpables == [], (
        "Un techo de tamaño escrito a mano dentro de una ruta. Si es política de "
        "producto, va a los ajustes de plataforma junto a `max_request_bytes`; si "
        "es defensa del proceso, va a una constante con nombre y un comentario "
        "que diga que no es configurable y por qué:\n  " + "\n  ".join(culpables)
    )


def test_el_rechazo_por_tamano_devuelve_la_cifra_aplicada():
    """`limit_bytes`, como el middleware, y no un `max_mb` escrito a mano.

    Un rechazo tiene que decir la cifra que se aplicó: si lleva una constante
    copiada, el día que el número cambie el mensaje seguirá diciendo el viejo.
    Flutter ya lee `limit_bytes` y no lee ninguna otra forma.
    """
    sospechosos = [
        f"{fichero.relative_to(RUTAS).as_posix()}"
        for fichero in sorted(RUTAS.rglob("*.py"))
        for texto in [fichero.read_text(encoding="utf-8")]
        if "max_mb" in texto or "max_total_mb" in texto
    ]
    assert sospechosos == [], (
        f"usa extra={{'limit_bytes': ...}} en: {sospechosos}"
    )
