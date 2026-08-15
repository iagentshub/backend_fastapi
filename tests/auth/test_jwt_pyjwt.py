"""Tests de la capa JWT tras la migración de python-jose a PyJWT.

Cubren lo que el cambio introduce (claims `iss`/`aud`, aviso de secreto corto)
y lo que no debe cambiar (tokens antiguos siguen valiendo mientras no expiren).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth.passwords import (
    _MIN_SECRET_BYTES,
    TokenClaims,
    _secret,
    create_token,
    decode_claims,
    decode_token,
)
from app.config.session import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER

# ── Emisión ────────────────────────────────────────────────────────────────────


def test_el_token_lleva_issuer_y_audience(patch_data_dir):
    payload = jwt.decode(
        create_token("alice"),
        _secret(),
        algorithms=[JWT_ALGORITHM],
        options={"verify_aud": False},
    )
    assert payload["iss"] == JWT_ISSUER
    assert payload["aud"] == JWT_AUDIENCE


def test_ida_y_vuelta(patch_data_dir):
    claims = decode_claims(create_token("alice", "group-9"))
    assert isinstance(claims, TokenClaims)
    assert (claims.username, claims.group_id) == ("alice", "group-9")
    assert claims.iat is not None


# ── Verificación ───────────────────────────────────────────────────────────────


def test_firma_invalida_se_rechaza(patch_data_dir):
    ahora = datetime.now(timezone.utc)
    ajeno = jwt.encode(
        {
            "sub": "admin",
            "gid": "admin",
            "iat": ahora,
            "exp": ahora + timedelta(hours=1),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        "otro-secreto-completamente-distinto-y-largo",
        algorithm=JWT_ALGORITHM,
    )
    assert decode_claims(ajeno) is None
    assert decode_token(ajeno) is None


def test_token_expirado_se_rechaza(patch_data_dir):
    """PyJWT valida `exp` por su cuenta; comprobamos que no se nos escapa."""
    pasado = datetime.now(timezone.utc) - timedelta(hours=2)
    caducado = jwt.encode(
        {"sub": "alice", "iat": pasado, "exp": pasado + timedelta(hours=1)},
        _secret(),
        algorithm=JWT_ALGORITHM,
    )
    assert decode_claims(caducado) is None


def test_issuer_ajeno_se_rechaza(patch_data_dir):
    """El motivo de emitir `iss`: un token de otro sistema con el mismo secreto."""
    ahora = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "alice",
            "iat": ahora,
            "exp": ahora + timedelta(hours=1),
            "iss": "otro-producto",
            "aud": JWT_AUDIENCE,
        },
        _secret(),
        algorithm=JWT_ALGORITHM,
    )
    assert decode_claims(token) is None


def test_audience_ajena_se_rechaza(patch_data_dir):
    ahora = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "alice",
            "iat": ahora,
            "exp": ahora + timedelta(hours=1),
            "iss": JWT_ISSUER,
            "aud": "otra-api",
        },
        _secret(),
        algorithm=JWT_ALGORITHM,
    )
    assert decode_claims(token) is None


def test_token_sin_iss_ni_aud_sigue_valiendo(patch_data_dir):
    """Compatibilidad: los tokens emitidos ANTES de la migración no llevan
    `iss`/`aud`. Exigirlos habría cerrado la sesión a todo el mundo en el
    despliegue."""
    ahora = datetime.now(timezone.utc)
    antiguo = jwt.encode(
        {
            "sub": "alice",
            "gid": "group-1",
            "iat": ahora,
            "exp": ahora + timedelta(hours=1),
        },
        _secret(),
        algorithm=JWT_ALGORITHM,
    )
    claims = decode_claims(antiguo)
    assert claims is not None
    assert (claims.username, claims.group_id) == ("alice", "group-1")


@pytest.mark.parametrize("basura", ["", "no-es-un-jwt", "a.b.c", "..", "x" * 200])
def test_basura_no_revienta(patch_data_dir, basura):
    assert decode_claims(basura) is None
    assert decode_token(basura) is None


def test_token_sin_sub_se_rechaza(patch_data_dir):
    """Sin sujeto no hay identidad que devolver, aunque la firma sea válida."""
    ahora = datetime.now(timezone.utc)
    sin_sub = jwt.encode(
        {"iat": ahora, "exp": ahora + timedelta(hours=1)},
        _secret(),
        algorithm=JWT_ALGORITHM,
    )
    assert decode_claims(sin_sub) is None


# ── Longitud del secreto (RFC 7518 §3.2) ───────────────────────────────────────


@pytest.fixture()
def log_flog():
    """Captura lo que flog escribe.

    `capsys` no sirve: el StreamHandler de flog se construyó al importar el
    módulo, apuntando al stdout de entonces. Se añade un handler propio, como
    en tests/utils/test_flog.py.
    """
    import logging
    from io import StringIO

    import app.utils.flog as flog_mod

    buf = StringIO()
    h = logging.StreamHandler(buf)
    flog_mod._L.addHandler(h)
    yield buf
    flog_mod._L.removeHandler(h)
    h.close()


def test_avisa_de_secreto_corto(patch_data_dir, monkeypatch, log_flog):
    """python-jose aceptaba una clave HMAC por debajo del tamaño del hash sin
    decir nada. PyJWT avisa, y aquí se convierte en una línea de log accionable."""
    import app.auth.passwords as passwords

    monkeypatch.setenv("GAIA_AGENTS_SECRET", "corto")
    monkeypatch.setattr(passwords, "_secreto_corto_avisado", False)
    _secret()
    salida = log_flog.getvalue()
    assert "RFC 7518" in salida
    assert str(_MIN_SECRET_BYTES) in salida


def test_el_aviso_no_se_repite(patch_data_dir, monkeypatch, log_flog):
    """_secret() se llama en cada petición: el aviso no puede inundar el log."""
    import app.auth.passwords as passwords

    monkeypatch.setenv("GAIA_AGENTS_SECRET", "corto")
    monkeypatch.setattr(passwords, "_secreto_corto_avisado", False)
    for _ in range(5):
        _secret()
    assert log_flog.getvalue().count("RFC 7518") == 1


def test_secreto_largo_no_avisa(patch_data_dir, monkeypatch, log_flog):
    import app.auth.passwords as passwords

    monkeypatch.setenv("GAIA_AGENTS_SECRET", "x" * _MIN_SECRET_BYTES)
    monkeypatch.setattr(passwords, "_secreto_corto_avisado", False)
    _secret()
    assert "RFC 7518" not in log_flog.getvalue()


def test_el_secreto_de_los_tests_cumple_el_minimo(patch_data_dir):
    """Si baja de 32 bytes, PyJWT llena la salida de la suite de avisos."""
    assert len(_secret().encode("utf-8")) >= _MIN_SECRET_BYTES
