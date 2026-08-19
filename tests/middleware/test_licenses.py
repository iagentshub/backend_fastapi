"""Tests para app.middleware.licenses — caché de billing_enabled.

Lo que se protege aquí es que _billing_enabled() no vuelva a leer y parsear
settings.json en cada petición (lo hacía, síncrono y dentro del event loop),
sin quedarse por ello con un valor rancio cuando los settings cambian.
"""

from __future__ import annotations

import json

import pytest

import app.middleware.licenses as lic


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Apunta SETTINGS_FILE a un fichero temporal y limpia el caché del módulo."""
    f = tmp_path / "settings.json"
    monkeypatch.setattr(lic, "SETTINGS_FILE", f)
    monkeypatch.setattr(lic, "_cache", None)

    def write(**data) -> None:
        f.write_text(json.dumps(data), encoding="utf-8")

    return f, write


def test_el_modulo_apunta_al_settings_de_los_tests():
    """Guardia del binding: licenses.SETTINGS_FILE debe ser el de tmp_data_dir.

    licenses.py importa SETTINGS_FILE por valor. Si el módulo se importa durante
    la colección de pytest, el binding queda en el directorio de colección y
    _billing_enabled() lee un fichero inexistente: la puerta de licencias no se
    activa jamás y los tests que esperan 403 reciben 200 en silencio. El
    conftest lo parchea; esto lo verifica.
    """
    import app.config.data as cfg

    assert lic.SETTINGS_FILE == cfg.SETTINGS_FILE
    assert lic.SETTINGS_FILE.exists(), (
        f"settings.json de los tests no existe en {lic.SETTINGS_FILE}"
    )


def test_sin_fichero_no_hay_billing(settings):
    assert lic._billing_enabled() is False


def test_lee_el_valor(settings):
    _f, write = settings
    write(billing_enabled=True)
    assert lic._billing_enabled() is True


def test_no_relee_el_fichero_si_no_cambia(settings, monkeypatch):
    """El caso que motivó el cambio: 100 peticiones, una sola lectura."""
    f, write = settings
    write(billing_enabled=True)

    lecturas = 0
    real = type(f).read_text

    def contar(self, *a, **kw):
        nonlocal lecturas
        lecturas += 1
        return real(self, *a, **kw)

    monkeypatch.setattr(type(f), "read_text", contar)

    for _ in range(100):
        assert lic._billing_enabled() is True
    assert lecturas == 1, f"settings.json se leyó {lecturas} veces en 100 peticiones"


def test_invalidar_hace_releer(settings):
    """El contrato: quien escribe settings.json invalida (lo hace _write_platform_cfg)."""
    _f, write = settings
    write(billing_enabled=False)
    assert lic._billing_enabled() is False

    write(billing_enabled=True)
    lic.invalidate_billing_cache()
    assert lic._billing_enabled() is True


def test_el_escritor_de_settings_invalida_el_cache(settings, monkeypatch):
    """Comprobación de extremo a extremo: el escritor real mantiene el caché al día.

    Es lo que impide que activar la facturación desde el panel de admin no surta
    efecto hasta reiniciar el proceso.
    """
    import app.config.data as cfg
    from app.services.platform_settings import _write_platform_cfg

    f, write = settings
    write(billing_enabled=False)
    assert lic._billing_enabled() is False

    monkeypatch.setattr(cfg, "SETTINGS_FILE", f)
    _write_platform_cfg({"billing_enabled": True})

    assert lic._billing_enabled() is True, (
        "_write_platform_cfg no invalidó el caché: el cambio no surtiría efecto"
    )


def test_un_fichero_ilegible_no_se_cachea(settings):
    """Cerrar la puerta por un error de lectura no debe quedar grabado."""
    f, write = settings
    f.write_text("{roto", encoding="utf-8")
    assert lic._billing_enabled() is False

    write(billing_enabled=True)
    assert lic._billing_enabled() is True, "se cacheó el fallo de lectura"


def test_json_corrupto_cierra_la_puerta(settings):
    f, _write = settings
    f.write_text("{no es json", encoding="utf-8")
    assert lic._billing_enabled() is False
