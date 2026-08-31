"""El explorador de tablas del panel enmascara lo que dice que enmascara.

Era una lista negra de siete nombres literales comparados por igualdad exacta,
dentro del propio endpoint. Una lista negra de secretos solo es correcta el día
que se escribe: desde entonces habían entrado `refresh_hash`,
`prev_refresh_hash`, `token_hash`, `code_hash`, `p256dh` y `auth`, y ninguna
lleva «token» ni «secret» como nombre completo. Dos de los siete, además, ya no
correspondían a ninguna columna del esquema.

No hay escalada de privilegio: esto es `require_admin` de principio a fin, y un
administrador de la instalación ya puede llegar a la base de datos por otros
caminos. Lo que fallaba es que la pantalla declara que enmascara lo sensible.
"""

from __future__ import annotations

import re

import pytest

from app.sql import SQL_DIR
from app.storage.schema import TABLAS, columnas_sensibles

# Nombres con forma de credencial. Si aparece una columna que encaje y no esté
# declarada, es que la próxima tabla volvió a colarse.
#
# El patrón busca el sustantivo entero y no la subcadena: `tokens_in` y
# `tokens_out` son contadores de consumo de LLM, `token_daily.tokens` también, y
# `reset_token_expires` es una fecha. Un patrón más ancho los marcaría a los
# nueve y la guarda acabaría desactivada por ruidosa.
FORMA_DE_CREDENCIAL = re.compile(
    r"^(.*_hash|.*_secret|secret.*|.*_token|token|p256dh|auth)$", re.IGNORECASE
)

# Columnas que encajan con el patrón y no son credenciales, con el motivo.
# Sin esta lista la guarda se vuelve ruido y se acaba desactivando entera.
EXCEPCIONES = {
    # Un hash de contenido es la identidad de unos bytes, no un secreto: existe
    # justamente para poder cotejarlo, y enseñarlo es su función.
    ("resource_source_links", "content_hash"),
}

COLUMNA = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s+", re.MULTILINE)


def _columnas_del_esquema():
    for tabla in TABLAS:
        ddl = (SQL_DIR / "schema" / f"{tabla}.sql").read_text(encoding="utf-8")
        # Solo el bloque del CREATE TABLE: los índices de después repiten
        # nombres de columna en otra forma.
        cuerpo = ddl.split(");")[0]
        for nombre in COLUMNA.findall(cuerpo):
            yield tabla, nombre


def test_toda_columna_con_forma_de_credencial_esta_declarada():
    declaradas = columnas_sensibles()
    sin_declarar = [
        f"{tabla}.{columna}"
        for tabla, columna in _columnas_del_esquema()
        if FORMA_DE_CREDENCIAL.fullmatch(columna)
        and (tabla, columna) not in EXCEPCIONES
        and columna not in declaradas.get(tabla, frozenset())
    ]
    assert sin_declarar == [], (
        "Añade `-- sensitive-columns:` al DDL de estas columnas, o justifícalas "
        f"en EXCEPCIONES: {sin_declarar}"
    )


def test_lo_declarado_existe_de_verdad():
    """Dos de los siete nombres de la lista negra ya no eran ninguna columna."""
    reales = {(t, c) for t, c in _columnas_del_esquema()}
    fantasmas = [
        f"{tabla}.{columna}"
        for tabla, columnas in columnas_sensibles().items()
        for columna in columnas
        if (tabla, columna) not in reales
    ]
    assert fantasmas == [], f"declaradas pero inexistentes: {fantasmas}"


def test_el_explorador_no_devuelve_la_columna_oculta(admin_client):
    r = admin_client.get("/api/admin/metadata/tables/users/data")
    assert r.status_code == 200, r.text
    assert "password_hash" not in r.json()["columns"]


def test_el_buscador_no_es_un_oraculo_de_confirmacion(admin_client):
    """El LIKE se construía sobre todas las columnas, ocultas incluidas.

    La columna salía como «[oculto]» pero `total` respondía con cuántas filas
    casaban, así que se podía confirmar un valor concreto sin verlo nunca.
    """
    fila = admin_client.get("/api/admin/metadata/tables/users/data").json()
    assert fila["total"] >= 1

    # El hash del admin existe en la tabla; buscar por su prefijo no puede
    # devolver ninguna coincidencia.
    r = admin_client.get(
        "/api/admin/metadata/tables/users/data", params={"q": "$2b$"}
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0, "el buscador sigue mirando dentro de la columna oculta"


def test_un_blob_sale_como_tamano_y_no_como_texto(admin_client):
    """`str(row[i])` sobre un BLOB devolvía la representación de sus bytes."""
    from app.api.routes.admin.stats import _valor_visible

    assert _valor_visible(b"12345") == "@5 bytes@"
    assert _valor_visible(memoryview(b"abc")) == "@3 bytes@"
    assert _valor_visible(None) is None
    assert _valor_visible(7) == "7"


@pytest.mark.parametrize("tabla", ["sessions", "personal_access_tokens", "push_subscriptions"])
def test_las_tablas_que_faltaban_ahora_declaran(tabla):
    assert columnas_sensibles().get(tabla), f"{tabla} sigue sin declarar nada"
