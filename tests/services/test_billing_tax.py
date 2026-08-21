"""Normalización del país y del NIF-IVA antes de llamar a Stripe (FIN-01)."""

from __future__ import annotations

import pytest

from app.services.billing_tax import (
    TaxIdentityError,
    normalize_country,
    normalize_tax_id,
)


@pytest.mark.parametrize("entrada,esperado", [
    ("ES", "ES"),
    ("es", "ES"),
    ("  fr  ", "FR"),
])
def test_el_pais_se_normaliza_a_mayusculas(entrada, esperado):
    assert normalize_country(entrada) == esperado


@pytest.mark.parametrize("entrada", ["", "   ", "España", "E", "ESP", "12"])
def test_el_pais_mal_formado_se_rechaza_con_su_campo(entrada):
    with pytest.raises(TaxIdentityError) as exc:
        normalize_country(entrada)
    assert exc.value.field == "country"
    assert exc.value.code == "invalid_field"


def test_sin_nif_no_hay_identificador():
    """Es opcional: sin él se cobra como consumidor, con el IVA del país."""
    assert normalize_tax_id("", "ES") is None
    assert normalize_tax_id("   ", "ES") is None


@pytest.mark.parametrize("entrada,esperado", [
    ("ESA12345678", "ESA12345678"),
    ("es a1234567 j", "ESA1234567J"),
    ("A-1234567.J", "ESA1234567J"),      # sin prefijo: se antepone el país
    ("DE123456789", "DE123456789"),      # empresa alemana comprando desde ES
])
def test_el_nif_se_limpia_y_se_le_antepone_el_pais(entrada, esperado):
    assert normalize_tax_id(entrada, "ES") == ("eu_vat", esperado)


@pytest.mark.parametrize("pais", ["US", "MX", "JP"])
def test_fuera_de_la_ue_no_se_registra_nif(pais):
    """Ceiling declarado: solo eu_vat. El cliente de fuera paga como consumidor."""
    with pytest.raises(TaxIdentityError) as exc:
        normalize_tax_id("12-3456789", pais)
    assert exc.value.code == "tax_id_country_unsupported"
    assert exc.value.field == "tax_id"


def test_nif_con_prefijo_de_pais_no_comunitario():
    """El país declarado es de la UE pero el número dice otra cosa."""
    with pytest.raises(TaxIdentityError) as exc:
        normalize_tax_id("US123456789", "ES")
    assert exc.value.code == "tax_id_country_unsupported"


@pytest.mark.parametrize("entrada", ["ES", "ESA", "ESA1234567890123456"])
def test_nif_con_formato_imposible(entrada):
    with pytest.raises(TaxIdentityError) as exc:
        normalize_tax_id(entrada, "ES")
    assert exc.value.field == "tax_id"
