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


def test_compute_total_cents_business_self_hosted_adds_flat_fee():
    without_sh = compute_total_cents("business", 10, "month", False)["amount_cents"]
    with_sh = compute_total_cents("business", 10, "month", True)["amount_cents"]
    assert with_sh - without_sh == round(bp.SH_MONTHLY * 100)


def test_compute_total_cents_business_self_hosted_annual():
    without_sh = compute_total_cents("business", 10, "year", False)["amount_cents"]
    with_sh = compute_total_cents("business", 10, "year", True)["amount_cents"]
    assert with_sh - without_sh == round(bp.SH_ANNUAL * 100)


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


@pytest.mark.parametrize("caso", _TABLA["casos"], ids=lambda c: f"{c['tier']}-{c['seats']}-{c['interval']}-sh{int(c['self_hosted'])}")
def test_la_formula_reproduce_los_precios_publicados(caso):
    resultado = compute_total_cents(
        caso["tier"], caso["seats"], caso["interval"], caso["self_hosted"]
    )
    assert resultado["price_per_seat_cents"] == caso["price_per_seat_cents"], _RECORDATORIO
    assert resultado["amount_cents"] == caso["amount_cents"], _RECORDATORIO


@pytest.mark.parametrize("interval,esperado", sorted(_TABLA["add_on_self_hosted_cents"].items()))
def test_el_add_on_self_hosted_cuesta_lo_publicado(interval, esperado):
    seats = 10
    sin_sh = compute_total_cents("business", seats, interval, False)["amount_cents"]
    con_sh = compute_total_cents("business", seats, interval, True)["amount_cents"]
    assert con_sh - sin_sh == esperado, _RECORDATORIO
