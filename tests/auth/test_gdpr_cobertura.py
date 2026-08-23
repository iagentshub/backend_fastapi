"""El borrado y la exportación RGPD alcanzan a todo recurso con dueño.

La rutina de borrado se escribió cuando el producto tenía agentes, skills y
knowledge, y cada recurso nuevo —prompts, tools, memoria, packs— se añadió sin
volver a pasar por ella. Como ninguna de esas tablas declara `REFERENCES users`,
la base de datos tampoco arrastraba nada en cascada: el usuario desaparecía de
`users` y sus filas se quedaban con un `owner_id` que ya no apuntaba a nadie.

Los tests por tabla no cierran esa clase de fallo, porque el que falta es
siempre el de la tabla que nadie recordó. Estos dos leen el esquema y fallan
solos con el siguiente recurso que se añada.
"""

from __future__ import annotations

import re

from app.sql import SQL_DIR, secciones_de

SCHEMA_DIR = SQL_DIR / "schema"

TABLA = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
# `resource_source_links` llama a su columna `resource_owner_id`; varias tablas
# históricas usan `username` y las ejecuciones usan `started_by`. Las tres
# formas representan la propiedad que el borrado RGPD debe cubrir.
COLUMNA_DUEÑO = re.compile(
    r"^\s*((?:(?:[A-Za-z0-9_]+_)?owner_id)|username|started_by)\s",
    re.MULTILINE,
)

# Tablas con dueño que quedan deliberadamente fuera del borrado, con el motivo.
# Sin esta lista la guarda se vuelve ruido y se acaba desactivando entera.
EXCLUIDAS = {
    # Contenido de administración: el catálogo oficial no es del usuario que lo
    # registró, y borrarlo con su cuenta se llevaría por delante las fuentes de
    # las que dependen los recursos de todos los demás.
    "official_sources": "catálogo oficial, contenido de administración",
    # Borrador de importación con caducidad propia (`expires_at`): se limpia por
    # su ciclo de vida, no por el del usuario.
    "official_import_drafts": "borrador efímero con expiración propia",
    # Los logs tienen retención propia y conservan la mínima trazabilidad de
    # seguridad incluso después del borrado. El usuario no es dueño de la fila.
    "app_logs": "registro diagnóstico y de auditoría con retención propia",
}


def _tablas_con_dueño() -> dict[str, str]:
    """{tabla: columna de propiedad} leídas del DDL, no de una lista a mano."""
    encontradas: dict[str, str] = {}
    for fichero in sorted(SCHEMA_DIR.glob("*.sql")):
        ddl = fichero.read_text(encoding="utf-8")
        tabla = TABLA.search(ddl)
        columna = COLUMNA_DUEÑO.search(ddl)
        if tabla and columna:
            encontradas[tabla.group(1).lower()] = columna.group(1)
    return encontradas


def _tablas_borradas() -> set[str]:
    borradas = set()
    for cuerpo in secciones_de("queries/gdpr").values():
        for tabla in re.findall(
            r"DELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)", cuerpo, re.IGNORECASE
        ):
            borradas.add(tabla.lower())
    return borradas


def test_toda_tabla_con_dueño_se_borra_al_ejercer_el_derecho_de_supresión():
    faltan = set(_tablas_con_dueño()) - _tablas_borradas() - set(EXCLUIDAS)
    assert faltan == set(), (
        "Estas tablas guardan filas con dueño y el borrado RGPD no las toca: "
        f"{sorted(faltan)}. Añade su DELETE a app/sql/queries/gdpr.sql, o "
        "anótala en EXCLUIDAS con el motivo por el que no le corresponde."
    )


def test_las_exclusiones_siguen_existiendo():
    """Una exclusión de una tabla que ya no existe esconde a la siguiente."""
    fantasmas = set(EXCLUIDAS) - set(_tablas_con_dueño())
    assert fantasmas == set(), (
        f"EXCLUIDAS nombra tablas que ya no tienen dueño: {sorted(fantasmas)}"
    )


def test_la_columna_de_propiedad_es_la_que_filtra_el_borrado():
    """Borrar por la columna equivocada deja la fila y no avisa de nada."""
    consultas = secciones_de("queries/gdpr")
    con_dueño = _tablas_con_dueño()
    errores = []
    for nombre, cuerpo in consultas.items():
        objetivo = re.search(
            r"DELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)", cuerpo, re.IGNORECASE
        )
        if not objetivo:
            continue
        tabla = objetivo.group(1).lower()
        columna = con_dueño.get(tabla)
        # `users.username` es identidad, no una referencia a su dueño: la fila
        # raíz se borra por la PK canónica que usan todas las demás tablas.
        if tabla == "users":
            columna = "id"
        if columna and columna not in cuerpo:
            errores.append(f"{nombre} borra {tabla} sin filtrar por {columna}")
    assert errores == [], errores


def test_la_exportación_entrega_los_mismos_recursos_que_borra_el_purgado():
    """Las dos caras del RGPD salen de la misma lista de tablas.

    Entregar menos de lo que se borra es la forma silenciosa de incumplir el
    artículo 20: el ZIP llega bien formado y sin el recurso.
    """
    exportadas = set()
    for cuerpo in secciones_de("queries/gdpr_export").values():
        for tabla in re.findall(
            r"FROM\s+([A-Za-z_][A-Za-z0-9_]*)", cuerpo, re.IGNORECASE
        ):
            exportadas.add(tabla.lower())

    # Lo que se borra pero no se entrega, con el motivo de cada excepción.
    sin_exportar = {
        # Metadatos internos de sincronización con el catálogo oficial: no son
        # contenido del usuario, son el enlace al repositorio del que salió.
        "resource_source_links",
        # Instantáneas de versiones anteriores del propio recurso, que sí va en
        # el ZIP en su estado actual.
        "resource_versions",
        # Pertenencias y comparticiones: ya viajan en groups.json.
        "group_members",
        "group_invitations",
        "groups",
        "resource_group_shares",
        "resource_social",
        "llm_orchestrations",
        "llm_orchestration_bindings",
        # Código de emparejamiento de un solo uso y 60 segundos: es material de
        # autenticación efímero, no un dato portable.
            "vscode_auth_codes",
            # Lease efímero de exclusión mutua: no es contenido portable y se
            # elimina al terminar la ejecución o a los cinco minutos sin latido.
            "resource_executions",
            "users",
    }
    faltan = _tablas_borradas() - exportadas - sin_exportar
    assert faltan == set(), (
        "Se borran pero no se exportan: "
        f"{sorted(faltan)}. Añade la consulta a gdpr_export.sql o justifica "
        "la excepción."
    )
