"""Tests de app.config.maintenance — la cadencia de los bucles de fondo.

Lo que se protege aquí es el suelo. Estos números van directos a un
`asyncio.sleep` dentro de un `while True`: a 0 el bucle no purga más a menudo,
se queda girando sin ceder el control y quema una CPU por worker. Es el único
valor de todo `config/` cuyo peor caso no es una función apagada sino un
servidor caliente sin motivo aparente.
"""

from __future__ import annotations

import importlib

import pytest

import app.config.maintenance as maintenance


def _recargar(monkeypatch, **entorno) -> object:
    """El módulo lee el entorno al importarse; para probarlo hay que reimportar."""
    for var, valor in entorno.items():
        monkeypatch.setenv(var, valor)
    return importlib.reload(maintenance)


@pytest.fixture(autouse=True)
def _restaurar():
    """Deja el módulo como estaba: lo comparten app.py y startup_checks.

    Recarga a la entrada además de a la salida. En la salida el entorno del
    test todavía está puesto —monkeypatch lo deshace después—, así que esa
    recarga vuelve a leer los valores sucios; la de la entrada es la que
    garantiza empezar limpio.
    """
    importlib.reload(maintenance)
    yield
    importlib.reload(maintenance)


def test_los_valores_por_defecto_son_los_de_siempre():
    """Nadie que no configure nada debe notar que esto se volvió configurable."""
    assert maintenance.GDPR_PURGE_SECONDS == 6 * 3600
    assert maintenance.LOG_PURGE_SECONDS == 24 * 3600
    assert maintenance.RATELIMIT_PURGE_SECONDS == 6 * 3600
    assert maintenance.WORKFLOW_TICK_SECONDS == 30
    assert maintenance.WORKFLOW_PURGE_SECONDS == 3600
    assert maintenance.ANOMALIAS == []


def test_un_valor_valido_se_respeta(monkeypatch):
    m = _recargar(monkeypatch, GAIA_RATELIMIT_PURGE_HOURS="2")
    assert m.RATELIMIT_PURGE_SECONDS == 2 * 3600
    assert m.ANOMALIAS == []


def test_cero_no_es_purgar_constantemente_sino_quemar_una_cpu(monkeypatch):
    m = _recargar(monkeypatch, GAIA_RATELIMIT_PURGE_HOURS="0")
    assert m.RATELIMIT_PURGE_SECONDS == 60
    assert "GAIA_RATELIMIT_PURGE_HOURS" in m.ANOMALIAS


def test_un_valor_que_no_es_numero_cae_al_defecto(monkeypatch):
    m = _recargar(monkeypatch, GAIA_LOG_PURGE_HOURS="cada rato")
    assert m.LOG_PURGE_SECONDS == 24 * 3600
    assert "GAIA_LOG_PURGE_HOURS" in m.ANOMALIAS


def test_el_tick_de_workflows_tiene_su_propio_suelo(monkeypatch):
    """Cinco segundos, no sesenta: este bucle es el que despega ejecuciones colgadas."""
    m = _recargar(monkeypatch, GAIA_WORKFLOW_TICK_SECONDS="1")
    assert m.WORKFLOW_TICK_SECONDS == 5
    assert "GAIA_WORKFLOW_TICK_SECONDS" in m.ANOMALIAS


def test_la_correccion_no_se_aplica_en_silencio(monkeypatch):
    """El arranque tiene que decir que el valor pedido no es el que está en vigor."""
    import app.config.startup_checks as checks

    _recargar(monkeypatch, GAIA_GDPR_PURGE_HOURS="-4")
    resultado = next(c for c in checks.run_checks() if c.key == "maintenance_intervals")
    assert resultado.severity == "warning"
    assert "GAIA_GDPR_PURGE_HOURS" in resultado.variables


def test_sin_anomalias_el_check_esta_en_ok():
    import app.config.startup_checks as checks

    resultado = next(c for c in checks.run_checks() if c.key == "maintenance_intervals")
    assert resultado.severity == "ok"


def test_la_retencion_de_workflows_se_cuenta_en_ticks(monkeypatch):
    """El bucle purga cada N ticks; N sale de los dos intervalos, no de un 120 suelto."""
    m = _recargar(
        monkeypatch, GAIA_WORKFLOW_TICK_SECONDS="60", GAIA_WORKFLOW_PURGE_HOURS="2"
    )
    assert max(1, m.WORKFLOW_PURGE_SECONDS // m.WORKFLOW_TICK_SECONDS) == 120


def test_ningun_bucle_conserva_su_intervalo_escrito_a_mano():
    """Guarda: el sitio de estos números es config/, no el cuerpo del bucle."""
    import re
    from pathlib import Path

    import app

    raiz = Path(app.__path__[0])
    culpables = []
    for fichero in sorted(raiz.rglob("*.py")):
        for n, linea in enumerate(fichero.read_text(encoding="utf-8").splitlines(), 1):
            # Los sleeps cortos de Centinel son pausas dentro de una prueba de
            # carga en curso, no la cadencia de un bucle de mantenimiento.
            if re.search(r"asyncio\.sleep\(\s*\d+\s*\*\s*\d+", linea):
                culpables.append(f"{fichero.relative_to(raiz)}:{n}")
    assert not culpables, f"Intervalos escritos en el bucle: {culpables}"
