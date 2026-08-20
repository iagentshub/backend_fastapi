"""El invitado se borra entero, menos su rastro en el log.

Es la excepción deliberada al «todo lo suyo desaparece»: los recursos se van con
`purge_user_data`, pero `app_logs` no se toca. Sin eso, un invitado sería un
usuario del que no queda constancia de nada — y la demo es una ruta pública,
donde el registro es lo único que permite reconstruir un abuso después.

La retención de esas líneas la decide el admin, como la del resto del log.
"""

from __future__ import annotations

import asyncio

import pytest

from app.auth.gdpr import purge_user_data
from app.storage.db import PH, open_db


@pytest.fixture(autouse=True)
def log_en_la_bd_del_test(patch_data_dir, monkeypatch):
    """Reapunta el handler de logs a la base de este test.

    `flog` resuelve la ruta de la BD **al importarse** y se construye antes que
    casi todo el backend, así que en un test escribe en la base del directorio
    de colección, no en la del test (es el mismo aviso de los paths por valor
    que documenta CLAUDE.md). Sin esto, log y purga viven en bases distintas y
    el test no probaría nada de lo que dice.
    """
    from app.config import data as cfg
    from app.utils import flog

    handler = flog._DB_HANDLER
    assert handler is not None, "sin handler de BD no hay log que comprobar"
    handler.flush()
    handler._drop_conn()
    monkeypatch.setattr(handler, "_db", str(cfg.DB_FILE))
    yield
    handler.flush()
    handler._drop_conn()


def _lineas_de(usuario: str) -> list[tuple[str, str]]:
    async def _leer() -> list[tuple[str, str]]:
        async with open_db() as conn:
            filas = await conn.fetchall(
                f"SELECT ip, summary FROM app_logs WHERE username={PH}", (usuario,)
            )
            return [(f[0], f[1]) for f in filas]

    return asyncio.run(_leer())


def test_el_alta_deja_la_ip_junto_al_id_del_invitado(client):
    """La línea que ata la IP con el invitado que nació de ella.

    `_username_for_log` lee la cookie de la petición y en el alta todavía no
    hay: esa línea sale anónima. Por eso el handler registra el alta aparte.
    """
    from app.utils import flog

    r = client.post("/api/auth/guest")
    assert r.status_code == 200, r.text
    guest_id = r.json()["username"]

    flog.flush()
    lineas = _lineas_de(guest_id)
    altas = [(ip, texto) for ip, texto in lineas if "alta" in texto]
    assert altas, f"el alta de {guest_id} no dejó línea propia en el log"
    ip, texto = altas[0]
    assert ip not in ("", "-"), "el alta se registró sin IP"
    assert guest_id in texto


def test_el_log_del_invitado_sobrevive_a_su_borrado(client):
    from app.utils import flog

    guest_id = client.post("/api/auth/guest").json()["username"]
    creado = client.post(
        "/api/agents",
        json={"name": "deja rastro", "system_prompt": "x", "model": "gpt-4o"},
    )
    assert creado.status_code in (200, 201), creado.text

    flog.flush()
    antes = _lineas_de(guest_id)
    assert antes, "el invitado no dejó ninguna línea de log"

    asyncio.run(purge_user_data(guest_id))
    flog.flush()

    despues = _lineas_de(guest_id)
    assert len(despues) >= len(antes), (
        "la purga se llevó el rastro del invitado del log: es lo único que "
        "debe sobrevivirle"
    )
