"""País de facturación y NIF-IVA: validación previa a llamar a Stripe.

Stripe Tax necesita saber dónde está el cliente **antes** de crear la
suscripción: la factura se emite en el mismo momento (`default_incomplete` ya
genera el borrador) y sin ubicación responde `customer_tax_location_invalid`.
Por eso el país viaja en el cuerpo de /subscribe y no en los datos de
facturación del Payment Element, que se rellenan después de todo esto.

Aquí no se comprueba que el NIF exista —eso lo hace Stripe contra VIES—, solo
que la combinación país/identificador sea una que Stripe vaya a aceptar, para
poder devolver un error propio y traducible en vez de un 502 con el texto
crudo de la pasarela.
"""

from __future__ import annotations

import re

# Los 27 Estados miembros. Para Stripe todos comparten el mismo tipo de
# identificador, `eu_vat`, con el país en las dos primeras letras del propio
# número (ES..., DE..., FR...).
EU_COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK",
})

# ponytail: solo NIF-IVA de la UE. Fuera de ella Stripe tiene otras dos docenas
# de tipos (gb_vat, ch_vat, au_abn, us_ein...) y cada uno con su formato; el
# cliente de fuera de la UE paga como consumidor, que es correcto aunque sea
# una empresa. Ampliarlo es añadir entradas a un mapa país → tipo aquí.
_EU_VAT = "eu_vat"

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# Formato del NIF-IVA intracomunitario: dos letras de país y de 2 a 12
# caracteres alfanuméricos. Deliberadamente laxo — hay 27 formatos nacionales
# distintos y quien los valida de verdad es VIES a través de Stripe.
_VAT_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{2,12}$")


class TaxIdentityError(ValueError):
    """País o NIF-IVA que Stripe no aceptaría. El código es el de la API."""

    def __init__(self, code: str, message: str, field: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def normalize_country(raw: str) -> str:
    """ISO 3166-1 alfa-2 en mayúsculas.

    No se valida contra una lista de países: mantenerla al día sería una
    dependencia nueva para repetir un trabajo que Stripe ya hace, y un código
    de dos letras inexistente lo rechaza la pasarela con su propio error. Lo
    que sí se corta aquí es la forma, que es lo que distingue un país vacío o
    un "España" escrito a mano de un código real.
    """
    country = (raw or "").strip().upper()
    if not _COUNTRY_RE.match(country):
        raise TaxIdentityError(
            "invalid_field",
            "Indica tu país de facturación con su código de dos letras (ES, FR, DE…)",
            "country",
        )
    return country


def normalize_tax_id(raw: str, country: str) -> tuple[str, str] | None:
    """`(tipo, valor)` para `Customer.create_tax_id`, o `None` si no hay NIF.

    El NIF es opcional: sin él se cobra como consumidor, con el IVA del país
    declarado. Con uno válido de otro Estado miembro, Stripe aplica la
    inversión del sujeto pasivo y la factura sale sin IVA.
    """
    value = re.sub(r"[\s.\-]", "", (raw or "")).upper()
    if not value:
        return None

    if country not in EU_COUNTRIES:
        raise TaxIdentityError(
            "tax_id_country_unsupported",
            "Por ahora solo podemos registrar el NIF-IVA de empresas de la Unión Europea",
            "tax_id",
        )

    # Sin prefijo de país es el número nacional a secas (12345678A): se le
    # antepone el del país declarado, que es como lo espera Stripe.
    if not re.match(r"^[A-Z]{2}", value):
        value = country + value

    if not _VAT_RE.match(value):
        raise TaxIdentityError(
            "invalid_field",
            "El NIF-IVA no tiene un formato válido (por ejemplo ESA12345678)",
            "tax_id",
        )
    if value[:2] not in EU_COUNTRIES:
        raise TaxIdentityError(
            "tax_id_country_unsupported",
            "Por ahora solo podemos registrar el NIF-IVA de empresas de la Unión Europea",
            "tax_id",
        )
    return _EU_VAT, value
