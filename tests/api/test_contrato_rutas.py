"""La superficie de rutas de la API está congelada en un fichero (FE-05).

Hay tres clientes (React, Flutter y la extensión de VS Code) con el contrato
escrito a mano, cada uno en su repo. Renombrar o quitar una ruta aquí no rompe
ninguna compilación: se descubre en runtime, en producción y por el usuario.

Esto no genera tipos —eso pide cuerpos pydantic, que hoy no existen: casi todos
los handlers parsean el JSON a mano y el esquema no sabe qué campos aceptan—,
pero sí hace que un cambio de rutas SALGA EN EL DIFF y haya que aprobarlo.

Para actualizarlo tras un cambio deliberado:

    python -m pytest tests/api/test_contrato_rutas.py --actualizar-contrato
"""

from __future__ import annotations

import pathlib

CONTRATO = pathlib.Path(__file__).with_name("contrato_rutas.txt")

# Rutas de infraestructura, no del contrato con los clientes.
IGNORADAS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _rutas_actuales() -> list[str]:
    from app.api.app import create_app

    esquema = create_app().openapi()
    return sorted(
        f"{metodo.upper()} {ruta}"
        for ruta, operaciones in esquema["paths"].items()
        if ruta not in IGNORADAS
        for metodo in operaciones
    )


def test_la_superficie_de_rutas_no_ha_cambiado(patch_data_dir, pytestconfig):
    actuales = _rutas_actuales()

    if pytestconfig.getoption("--actualizar-contrato", default=False):
        CONTRATO.write_text("\n".join(actuales) + "\n", encoding="utf-8")
        return

    esperadas = [
        linea.strip()
        for linea in CONTRATO.read_text(encoding="utf-8").splitlines()
        if linea.strip()
    ]

    nuevas = sorted(set(actuales) - set(esperadas))
    borradas = sorted(set(esperadas) - set(actuales))
    assert not (nuevas or borradas), (
        "La superficie de la API cambió y hay tres clientes con el contrato a mano.\n"
        f"  Nuevas:   {nuevas}\n"
        f"  Borradas: {borradas}\n"
        "Si es intencionado: actualiza React, Flutter y la extensión, y luego\n"
        "  python -m pytest tests/api/test_contrato_rutas.py --actualizar-contrato"
    )
