"""Fórmula de precios — réplica exacta de frontend/pages/pricing/pricing.js.

El backend es la única fuente de verdad del monto a cobrar: nunca se acepta
un importe calculado por el cliente. Cualquier cambio en las constantes de
pricing.js debe reflejarse aquí también.
"""
from __future__ import annotations

DEV_PRICE = 9.0
BIZ_START = 7.50
FLOOR = DEV_PRICE * 0.50          # 4.50
ENT_THRESHOLD = 100
SH_MONTHLY = 400.0
MONTHS_ANNUAL = 10
SH_ANNUAL = SH_MONTHLY * MONTHS_ANNUAL  # 4000.0
SLOPE = (BIZ_START - FLOOR) / (ENT_THRESHOLD - 1)

VALID_TIERS = ("developer", "business")
VALID_INTERVALS = ("month", "year")


class InvalidPlanError(ValueError):
    """Combinación de tier/seats/interval fuera de rango de self-serve."""


def price_per_seat(seats: int) -> float:
    if seats <= 0:
        return 0.0
    if seats == 1:
        return DEV_PRICE
    return max(FLOOR, BIZ_START - SLOPE * (seats - 1))


def validate_plan(tier: str, seats: int) -> None:
    if tier == "developer":
        if seats != 1:
            raise InvalidPlanError("developer requiere seats == 1")
    elif tier == "business":
        if not (2 <= seats <= ENT_THRESHOLD):
            raise InvalidPlanError(f"business requiere 2 <= seats <= {ENT_THRESHOLD}")
    else:
        raise InvalidPlanError(f"tier no soporta self-serve: {tier}")


def compute_total_cents(tier: str, seats: int, interval: str, self_hosted: bool) -> dict:
    """Calcula el monto recurrente total en céntimos, más el desglose."""
    if interval not in VALID_INTERVALS:
        raise InvalidPlanError(f"interval inválido: {interval}")
    validate_plan(tier, seats)

    per_seat = price_per_seat(seats)
    monthly_base = seats * per_seat

    if interval == "month":
        seats_amount = monthly_base
        sh_amount = SH_MONTHLY if self_hosted else 0.0
    else:
        seats_amount = monthly_base * MONTHS_ANNUAL
        sh_amount = SH_ANNUAL if self_hosted else 0.0

    seats_cents = round(seats_amount * 100)
    sh_cents = round(sh_amount * 100)

    return {
        "price_per_seat_cents": round(per_seat * 100),
        "seats_amount_cents": seats_cents,
        "self_hosted_amount_cents": sh_cents,
        "amount_cents": seats_cents + sh_cents,
    }
