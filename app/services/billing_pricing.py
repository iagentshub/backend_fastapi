"""Fórmula de precios — fuente de verdad del importe a cobrar.

El backend nunca acepta un importe calculado por el cliente: lo recalcula
aquí a partir de tier, asientos e intervalo.

Estas constantes están DUPLICADAS en la calculadora pública del frontend
(frontend_react/src/routes/public/pricing-model.ts), que las necesita en
cliente para poder prerenderizarse. Si divergen, el precio anunciado deja
de ser el precio cobrado, y nada lo detecta: cualquier cambio aquí obliga
a tocar también ese fichero.

La referencia anterior de esta nota (frontend/pages/pricing/pricing.js) era
del frontend vanilla ya retirado; apuntaba a un fichero inexistente, así que
la garantía de sincronía que prometía no existía.

El add-on se llamaba `self_hosted` en todo el backend, y ese nombre describe
un permiso de despliegue: exactamente lo que la licencia AGPL-3.0 ya concede
gratis y no se puede cobrar. Lo que la página pública vende —y lleva tiempo
diciéndolo con todas las letras— es el SOPORTE sobre ese despliegue, con SLA
por contrato. El nombre del código era lo único que seguía describiendo el
producto antiguo, así que aquí es `sla_support`. En el cable siguen viajando
los dos nombres mientras Flutter y React no salgan; ver `subscribe`.

Solo se contrata con intervalo anual, que es lo que anuncia la página
(«Requiere contrato anual — no disponible en modalidad mensual»). El
calculador ofrecía añadirlo sobre una suscripción mensual y el alta lo
aceptaba, cobrando un contrato que la web dice que no existe.
"""

from __future__ import annotations

DEV_PRICE = 9.0
BIZ_START = 7.50
FLOOR = DEV_PRICE * 0.50  # 4.50
ENT_THRESHOLD = 100
MONTHS_ANNUAL = 10
# €400/mes facturados de una vez: el año son 10 mensualidades, como los asientos.
SLA_SUPPORT_MONTHLY = 400.0
SLA_SUPPORT_ANNUAL = SLA_SUPPORT_MONTHLY * MONTHS_ANNUAL  # 4000.0
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


def compute_total_cents(
    tier: str, seats: int, interval: str, sla_support: bool, *, new_contract: bool = True
) -> dict:
    """Calcula el monto recurrente total en céntimos, más el desglose.

    `new_contract=False` recalcula una suscripción ya existente y por eso no
    exige el intervalo anual del add-on: mientras se aceptó en mensual pudo
    firmarse alguna, y esas siguen vivas. Aplicarles la regla nueva no las
    corrige —les rompe el cambio de asientos con un 400 y sin arreglo posible
    desde la interfaz—. La restricción es para lo que se contrata a partir de
    ahora, que es donde importa.
    """
    if interval not in VALID_INTERVALS:
        raise InvalidPlanError(f"interval inválido: {interval}")
    validate_plan(tier, seats)
    if sla_support and interval != "year" and new_contract:
        raise InvalidPlanError("el soporte con SLA requiere contrato anual")

    per_seat = price_per_seat(seats)
    monthly_base = seats * per_seat

    if interval == "month":
        seats_amount = monthly_base
    else:
        seats_amount = monthly_base * MONTHS_ANNUAL

    seats_cents = round(seats_amount * 100)
    sla_cents = round(SLA_SUPPORT_ANNUAL * 100) if sla_support else 0

    return {
        "price_per_seat_cents": round(per_seat * 100),
        "seats_amount_cents": seats_cents,
        "sla_support_amount_cents": sla_cents,
        # El nombre viejo del mismo importe. Se queda mientras haya bundles
        # cacheados que lo lean: el backend sale antes que Flutter y React.
        "self_hosted_amount_cents": sla_cents,
        "amount_cents": seats_cents + sla_cents,
    }
