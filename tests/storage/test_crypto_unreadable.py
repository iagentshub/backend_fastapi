"""Un descifrado fallido se nota: nunca devuelve el ciphertext (mejora #04).

Antes, `decrypt()` capturaba el fallo y devolvía el valor cifrado tal cual, así
que `enc:gAAAAA…` acababa en la cabecera `Authorization` del proveedor LLM y el
usuario veía un 401 ajeno. Ahora el fallo es tipado (`DecryptionError`), el
storage vacía el campo y lo marca, y quien vaya a usar la credencial se niega.
"""
from __future__ import annotations

import pytest

from app.storage import crypto
from app.storage.accounts import AccountStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.crypto import (
    UNREADABLE_FIELDS,
    UNREADABLE_FLAG,
    DecryptionError,
    decrypt,
    encrypt,
)

_OTRO_SECRETO = "otro-secreto-de-pruebas-suficientemente-largo-0123456789"


@pytest.fixture()
def rotar_secreto(monkeypatch):
    """Devuelve una función que simula un secreto distinto al de cifrado."""

    def _rotar() -> None:
        monkeypatch.setenv("GAIA_AGENTS_SECRET", _OTRO_SECRETO)
        monkeypatch.setattr(crypto, "_fernet", None)

    return _rotar


def test_decrypt_lanza_con_secreto_rotado(rotar_secreto):
    cifrado = encrypt("sk-secreta")
    rotar_secreto()

    with pytest.raises(DecryptionError):
        decrypt(cifrado)


def test_decrypt_deja_pasar_valores_legacy_en_claro():
    assert decrypt("sk-en-claro") == "sk-en-claro"
    assert decrypt("") == ""


async def test_conexion_ilegible_se_marca_y_no_devuelve_ciphertext(
    patch_data_dir, rotar_secreto
):
    storage = ConnectionStorage()
    conn = await storage.save({"type": "openai", "api_key": "sk-secreta"})
    rotar_secreto()

    leida = await storage.get(conn["id"])
    assert leida["api_key"] == ""
    assert leida[UNREADABLE_FLAG] is True
    assert leida[UNREADABLE_FIELDS] == ["api_key"]

    listadas = await storage.list()
    assert listadas[0]["api_key"] == ""
    assert listadas[0][UNREADABLE_FLAG] is True


async def test_editar_una_conexion_ilegible_no_destruye_la_clave(
    patch_data_dir, monkeypatch
):
    """Cambiar el nombre con la clave ilegible conserva el ciphertext: vuelve
    a leerse en cuanto se restaura el secreto correcto."""
    storage = ConnectionStorage()
    original = await storage.save(
        {"type": "openai", "name": "V1", "api_key": "sk-secreta"}
    )
    secreto_bueno = crypto._fernet

    monkeypatch.setenv("GAIA_AGENTS_SECRET", _OTRO_SECRETO)
    monkeypatch.setattr(crypto, "_fernet", None)
    rota = await storage.get(original["id"])
    await storage.save({**rota, "name": "V2"})

    monkeypatch.setattr(crypto, "_fernet", secreto_bueno)
    recuperada = await storage.get(original["id"])
    assert recuperada["name"] == "V2"
    assert recuperada["api_key"] == "sk-secreta"
    assert UNREADABLE_FLAG not in recuperada


async def test_clave_nueva_reemplaza_a_la_ilegible(patch_data_dir, rotar_secreto):
    storage = ConnectionStorage()
    original = await storage.save({"type": "openai", "api_key": "sk-vieja"})
    rotar_secreto()

    rota = await storage.get(original["id"])
    await storage.save({**rota, "api_key": "sk-nueva"})

    arreglada = await storage.get(original["id"])
    assert arreglada["api_key"] == "sk-nueva"
    assert UNREADABLE_FLAG not in arreglada


async def test_cuenta_ilegible_se_marca_y_no_enmascara_nada(
    patch_data_dir, rotar_secreto
):
    storage = AccountStorage()
    cuenta = await storage.save(
        {"provider": "openai", "api_key": "sk-secreta"}, owner_id="alice"
    )
    rotar_secreto()

    leida = await storage.get(cuenta["id"], "alice")
    assert leida["api_key"] == ""
    assert leida[UNREADABLE_FLAG] is True

    listadas = await storage.list("alice")
    assert listadas[0]["api_key_masked"] == ""
    assert listadas[0][UNREADABLE_FLAG] is True


async def test_editar_una_cuenta_ilegible_no_destruye_la_clave(
    patch_data_dir, monkeypatch
):
    storage = AccountStorage()
    cuenta = await storage.save(
        {"provider": "openai", "api_key": "sk-secreta"}, owner_id="alice"
    )
    secreto_bueno = crypto._fernet

    monkeypatch.setenv("GAIA_AGENTS_SECRET", _OTRO_SECRETO)
    monkeypatch.setattr(crypto, "_fernet", None)
    rota = await storage.get(cuenta["id"], "alice")
    await storage.save({**rota, "name": "Renombrada"}, owner_id="alice")

    monkeypatch.setattr(crypto, "_fernet", secreto_bueno)
    recuperada = await storage.get(cuenta["id"], "alice")
    assert recuperada["name"] == "Renombrada"
    assert recuperada["api_key"] == "sk-secreta"
    assert UNREADABLE_FLAG not in recuperada


async def test_las_marcas_no_se_guardan_en_la_base_de_datos(patch_data_dir):
    """Un cliente que reenvíe las marcas en el payload no las persiste."""
    storage = ConnectionStorage()
    conn = await storage.save(
        {
            "type": "openai",
            "api_key": "sk-secreta",
            UNREADABLE_FLAG: True,
            UNREADABLE_FIELDS: ["api_key"],
        }
    )

    leida = await storage.get(conn["id"])
    assert UNREADABLE_FLAG not in leida
    assert leida["api_key"] == "sk-secreta"
