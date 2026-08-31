"""Ningún fichero de `app/` crece sin que se note.

El CLAUDE.md llevaba tiempo afirmando que «ningún fichero de app/ pasa de 600
líneas». No era verdad —había siete por encima, y dos de ellos en
`api/routes/`, que es justo donde esa afirmación nació— y nadie se enteró
porque no había nada que lo comprobara: la frase envejeció sola mientras los ficheros
crecían. Este test es lo que la hace cierta de aquí en adelante.

Las siete que ya estaban se quedan anotadas en DEUDA con su medida del día en
que se puso la guarda. No es una lista de permitidos abierta: **el número es un
techo**, así que un fichero de esa lista tampoco puede seguir engordando, y en
cuanto se parte se borra su entrada. Partirlos ahora habría sido tocar siete
zonas que este trabajo no revisó.
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

LIMITE = 600

# fichero -> líneas el día en que se puso la guarda (2026-08-25). Bajar estos
# números al partir un fichero; nunca subirlos.
DEUDA = {
    # 751 el día de la guarda. Bajó a 629 al separar el historial de versiones
    # en `resource_versions_history.py`: su docstring ya decía que el fichero
    # eran dos cosas. Sigue por encima del límite, así que sigue anotado.
    "api/routes/resource_management.py": 629,
    "storage/knowledge.py": 740,
    "storage/tool_storage.py": 719,
    "storage/migrations/steps/misc.py": 716,
    "utils/flog.py": 673,
}


def _medidas() -> dict[str, int]:
    # `as_posix()` y no `str()`: en Windows la ruta relativa sale con barras
    # invertidas, ninguna clave casaba con las de DEUDA —que se escriben con
    # `/`— y la guarda denunciaba los siete ficheros anotados como si fueran
    # nuevos. La suite corre en Windows y en CI, así que la separación tiene que
    # ser la misma en los dos sitios.
    return {
        fichero.relative_to(APP).as_posix(): len(
            fichero.read_text(encoding="utf-8").splitlines()
        )
        for fichero in APP.rglob("*.py")
        if "__pycache__" not in fichero.parts
    }


def test_ningun_fichero_nuevo_pasa_del_limite():
    medidas = _medidas()
    pasados = {
        ruta: lineas
        for ruta, lineas in medidas.items()
        if lineas > LIMITE and ruta not in DEUDA
    }

    assert pasados == {}, (
        f"Estos ficheros pasan de {LIMITE} líneas y no están en DEUDA:\n"
        + "\n".join(f"  {r}: {n}" for r, n in sorted(pasados.items()))
        + "\n\nDivídelos —el repo ya usa paquetes con `__init__.py` y mixins "
        "para esto— o, si es deuda que asumes a sabiendas, anótalos en DEUDA "
        "con su medida y el motivo."
    )


def test_la_deuda_conocida_no_crece():
    """Un fichero ya gordo tampoco puede engordar más."""
    medidas = _medidas()
    crecidos = {
        ruta: (tope, medidas[ruta])
        for ruta, tope in DEUDA.items()
        if ruta in medidas and medidas[ruta] > tope
    }

    assert crecidos == {}, (
        "Estos ficheros ya estaban por encima del límite y han crecido más:\n"
        + "\n".join(
            f"  {r}: {antes} → {ahora}" for r, (antes, ahora) in sorted(crecidos.items())
        )
        + "\n\nLo que se le añada a un fichero de DEUDA sale de ahí, no se suma."
    )


def test_la_deuda_no_lista_ficheros_ya_arreglados():
    """Al partir uno, su entrada se borra: si no, la lista protege a quien ya
    no lo necesita y el límite se vuelve papel mojado."""
    medidas = _medidas()
    sobran = {
        ruta: medidas.get(ruta)
        for ruta in DEUDA
        if ruta not in medidas or medidas[ruta] <= LIMITE
    }

    assert sobran == {}, (
        "Estas entradas de DEUDA ya no hacen falta: el fichero se partió o "
        f"desapareció. Bórralas.\n{sorted(sobran)}"
    )
