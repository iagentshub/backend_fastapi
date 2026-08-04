"""storage.py no debe quedarse con una copia de IS_PG.

`IS_PG` es un booleano, y la suite entera corre en SQLite gracias a que
conftest hace `monkeypatch.setattr(db, "IS_PG", False)`. Ese parcheo cambia el
atributo del módulo `db`. Si storage.py lo trajera por valor —`from
app.storage.db import IS_PG`— tendría su propio nombre, congelado en el
arranque, y el parcheo no le llegaría: en una máquina con DATABASE_URL
apuntando a Postgres, los tests construirían SQL con marcadores `$n` contra
SQLite.

Es la trampa que documenta CLAUDE.md, y ningún test de comportamiento la
detecta, porque el valor por defecto ya suele ser el correcto y el fallo solo
aparece en el entorno de otro. Por eso se comprueba la forma del módulo, no su
comportamiento: es el único momento en que la diferencia es visible.
"""

from __future__ import annotations

import app.storage.storage as storage_mod


def test_storage_no_tiene_una_copia_del_booleano():
    assert not hasattr(storage_mod, "IS_PG"), (
        "storage.IS_PG existe: alguien ha vuelto a importarlo por valor. "
        "Léelo como _db.IS_PG."
    )


def test_storage_llega_al_modulo_db_entero():
    """El alias tiene que ser el módulo, no un nombre suelto."""
    import app.storage.db as db_mod

    assert storage_mod._db is db_mod
