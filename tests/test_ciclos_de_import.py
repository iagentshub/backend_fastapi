"""Congela los ciclos de importación que existen, para que no aparezcan más.

Python deja convivir con un ciclo si uno de los dos imports se mete dentro de
una función, así que un ciclo nuevo no rompe nada al aparecer: se descubre
meses después, cuando alguien intenta partir el módulo y no puede. De ahí que
haya 131 imports diferidos en `app/` y que casi ninguno esté rompiendo un ciclo
de verdad — se metieron dentro de funciones por costumbre, y esa costumbre
esconde los pocos que sí lo hacen.

Los tres de abajo son los que hay hoy. Son la jerarquía de modelos de agente:
una clase base que conoce a sus subclases para construirlas, que es normal y
no se persigue.

Si añades un ciclo a propósito, añádelo aquí y explica por qué. Si aparece sin
querer, es que un módulo ha empezado a depender de quien depende de él.
"""

from __future__ import annotations

import ast
import pathlib

# Se cuentan TODOS los imports, también los diferidos dentro de funciones: un
# ciclo escondido en un import diferido sigue siendo un ciclo.
CICLOS_CONOCIDOS = {
    frozenset({"app.models.agent", "app.models.openai_agent"}),
    frozenset({"app.models.agent", "app.models.github_agent"}),
    frozenset({"app.models.agent", "app.models.claude_agent"}),
}

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "app"


def _grafo() -> dict[str, set[str]]:
    grafo: dict[str, set[str]] = {}
    for ruta in RAIZ.rglob("*.py"):
        rel = ruta.relative_to(RAIZ).as_posix()
        modulo = ("app." + rel[:-3].replace("/", ".")).removesuffix(".__init__")
        try:
            arbol = ast.parse(ruta.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover
            continue
        destinos: set[str] = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                destinos |= {a.name for a in nodo.names if a.name.startswith("app.")}
            elif isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith(
                "app."
            ):
                destinos.add(nodo.module or "")
        grafo[modulo] = destinos
    return grafo


def _ciclos(grafo: dict[str, set[str]]) -> set[frozenset[str]]:
    encontrados: set[frozenset[str]] = set()
    estado: dict[str, int] = {}

    def visitar(modulo: str, pila: list[str]) -> None:
        estado[modulo] = 1
        for destino in grafo.get(modulo, ()):
            if destino not in grafo:
                continue
            if estado.get(destino) == 1:
                encontrados.add(frozenset(pila[pila.index(destino) :] + [destino]))
            elif destino not in estado:
                visitar(destino, pila + [destino])
        estado[modulo] = 2

    for modulo in grafo:
        if modulo not in estado:
            visitar(modulo, [modulo])
    return encontrados


def test_no_hay_ciclos_de_import_nuevos():
    encontrados = _ciclos(_grafo())
    nuevos = encontrados - CICLOS_CONOCIDOS
    assert not nuevos, "Ciclos de importación nuevos:\n" + "\n".join(
        "  " + " <-> ".join(sorted(c)) for c in sorted(nuevos, key=sorted)
    )


def test_los_ciclos_congelados_siguen_existiendo():
    """Si uno se arregla, hay que quitarlo de la lista — si no, deja de vigilar."""
    encontrados = _ciclos(_grafo())
    resueltos = CICLOS_CONOCIDOS - encontrados
    assert not resueltos, (
        "Ciclos ya resueltos, bórralos de CICLOS_CONOCIDOS:\n"
        + "\n".join(
            "  " + " <-> ".join(sorted(c)) for c in sorted(resueltos, key=sorted)
        )
    )
