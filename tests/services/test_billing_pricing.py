import json
from pathlib import Path

import pytest

from app.services import billing_pricing as bp
from app.services.billing_pricing import (
    InvalidPlanError,
    compute_total_cents,
    price_per_seat,
)


def test_price_per_seat_developer():
    assert price_per_seat(1) == bp.DEV_PRICE


def test_price_per_seat_business_decreasing():
    p2 = price_per_seat(2)
    p50 = price_per_seat(50)
    p100 = price_per_seat(100)
    assert p2 == bp.BIZ_START - bp.SLOPE
    assert p2 > p50 > p100
    assert p100 == bp.FLOOR


def test_price_per_seat_floor_never_below():
    assert price_per_seat(bp.ENT_THRESHOLD) == bp.FLOOR


def test_compute_total_cents_developer_monthly():
    result = compute_total_cents("developer", 1, "month", False)
    assert result["amount_cents"] == round(bp.DEV_PRICE * 100)


def test_compute_total_cents_developer_annual_matches_months_annual():
    monthly = compute_total_cents("developer", 1, "month", False)["amount_cents"]
    annual = compute_total_cents("developer", 1, "year", False)["amount_cents"]
    assert annual == monthly * bp.MONTHS_ANNUAL


def test_el_soporte_con_sla_suma_la_cuota_anual():
    sin_sla = compute_total_cents("business", 10, "year", False)["amount_cents"]
    con_sla = compute_total_cents("business", 10, "year", True)["amount_cents"]
    assert con_sla - sin_sla == round(bp.SLA_SUPPORT_ANNUAL * 100)


def test_el_soporte_con_sla_no_se_contrata_en_mensual():
    # La página anuncia «requiere contrato anual — no disponible en modalidad
    # mensual», y el alta lo aceptaba igual: 400 € al mes de un contrato que
    # la web dice que no existe.
    with pytest.raises(InvalidPlanError):
        compute_total_cents("business", 10, "month", True)


def test_una_suscripcion_mensual_ya_firmada_se_puede_recalcular():
    # Mientras el add-on se aceptó en mensual pudo firmarse alguna. Aplicarle
    # la regla nueva no la corrige: le rompe el cambio de asientos con un 400
    # que nadie puede arreglar desde la interfaz.
    totals = compute_total_cents("business", 10, "month", True, new_contract=False)
    assert totals["amount_cents"] > 0


def test_el_desglose_conserva_el_nombre_viejo_del_add_on():
    # Flutter y React salen después que el backend, y sus bundles cacheados
    # leen `self_hosted_amount_cents`. Retirarlo aquí les deja el total sin
    # desglosar en la pantalla de pago.
    totals = compute_total_cents("business", 10, "year", True)
    assert totals["self_hosted_amount_cents"] == totals["sla_support_amount_cents"]


def test_developer_requires_single_seat():
    with pytest.raises(InvalidPlanError):
        compute_total_cents("developer", 2, "month", False)


def test_business_requires_seat_range():
    with pytest.raises(InvalidPlanError):
        compute_total_cents("business", 1, "month", False)
    with pytest.raises(InvalidPlanError):
        compute_total_cents("business", 101, "month", False)


def test_enterprise_not_self_serve():
    with pytest.raises(InvalidPlanError):
        compute_total_cents("enterprise", 200, "month", False)


def test_invalid_interval():
    with pytest.raises(InvalidPlanError):
        compute_total_cents("developer", 1, "week", False)


# ── Precios publicados (REL-03) ───────────────────────────────────────────────
# Todo lo de arriba comprueba la fórmula contra SUS PROPIAS constantes: subir
# DEV_PRICE de 9 a 12 no rompía ni un test, aquí ni en frontend_react, y el
# precio anunciado dejaba de ser el cobrado sin que nada lo dijera.
#
# `published-prices.json` son importes absolutos y el mismo fichero, byte a
# byte, vive también en frontend_react/src/routes/public/. Ver su cabecera.

_TABLA = json.loads(
    (Path(__file__).parent / "published-prices.json").read_text(encoding="utf-8")
)

_RECORDATORIO = (
    "Has cambiado la fórmula de precios. Actualiza published-prices.json en "
    "ESTE repositorio y la copia idéntica de frontend_react/src/routes/public/, "
    "o el precio anunciado dejará de ser el cobrado."
)


@pytest.mark.parametrize("caso", _TABLA["casos"], ids=lambda c: f"{c['tier']}-{c['seats']}-{c['interval']}-sla{int(c['sla_support'])}")
def test_la_formula_reproduce_los_precios_publicados(caso):
    resultado = compute_total_cents(
        caso["tier"], caso["seats"], caso["interval"], caso["sla_support"]
    )
    assert resultado["price_per_seat_cents"] == caso["price_per_seat_cents"], _RECORDATORIO
    assert resultado["amount_cents"] == caso["amount_cents"], _RECORDATORIO


@pytest.mark.parametrize("interval,esperado", sorted(_TABLA["add_on_sla_support_cents"].items()))
def test_el_soporte_con_sla_cuesta_lo_publicado(interval, esperado):
    seats = 10
    sin_sla = compute_total_cents("business", seats, interval, False)["amount_cents"]
    con_sla = compute_total_cents("business", seats, interval, True)["amount_cents"]
    assert con_sla - sin_sla == esperado, _RECORDATORIO
