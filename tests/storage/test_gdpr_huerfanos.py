"""La limpieza retroactiva de filas huérfanas del borrado RGPD (migración 28).

Es la única migración destructiva del registro: borra filas sin poder
recuperarlas. Los tres casos que la hacen segura —o peligrosa si se tocan— son
el catálogo público, el propietario legacy `admin` y la instalación sin
usuarios, donde "no está en users" es cierto para absolutamente todo.
"""

from __future__ import annotations

import sqlite3

import aiosqlite

from app.storage.migrations.steps.misc import _gdpr_orphan_resources_sqlite

_ESQUEMA = """
CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT);
CREATE TABLE prompts (id TEXT, owner_id TEXT, alias TEXT);
CREATE TABLE tools (id TEXT, owner_id TEXT);
CREATE TABLE memory_files (id TEXT, owner_id TEXT);
CREATE TABLE knowledge_packs (id TEXT, owner_id TEXT);
CREATE TABLE resource_versions (id TEXT, owner_id TEXT);
CREATE TABLE resource_source_links (source_id TEXT, resource_owner_id TEXT);
"""


async def _db(tmp_path, con_usuarios: bool = True):
    conn = await aiosqlite.connect(tmp_path / "huerfanos.db")
    conn.row_factory = sqlite3.Row
    await conn.executescript(_ESQUEMA)
    if con_usuarios:
        await conn.execute("INSERT INTO users (id, username) VALUES ('u-viva', 'ana')")
    return conn


async def _ids(conn, tabla: str, columna: str = "owner_id") -> list[str]:
    cursor = await conn.execute(f"SELECT {columna} FROM {tabla}")  # noqa: S608
    return [row[0] for row in await cursor.fetchall()]


async def test_borra_las_filas_de_un_usuario_que_ya_no_existe(tmp_path):
    conn = await _db(tmp_path)
    await conn.execute("INSERT INTO prompts VALUES ('p1', 'u-borrada', 'a')")
    await conn.execute("INSERT INTO tools VALUES ('t1', 'u-borrada')")
    await conn.execute("INSERT INTO memory_files VALUES ('m1', 'u-borrada')")
    await conn.execute("INSERT INTO knowledge_packs VALUES ('k1', 'u-borrada')")
    await conn.execute("INSERT INTO resource_versions VALUES ('v1', 'u-borrada')")
    await conn.execute("INSERT INTO resource_source_links VALUES ('s1', 'u-borrada')")

    await _gdpr_orphan_resources_sqlite(conn)

    for tabla in ("prompts", "tools", "memory_files", "knowledge_packs",
                  "resource_versions"):
        assert await _ids(conn, tabla) == []
    assert await _ids(conn, "resource_source_links", "resource_owner_id") == []
    await conn.close()


async def test_no_toca_lo_de_un_usuario_vivo(tmp_path):
    conn = await _db(tmp_path)
    await conn.execute("INSERT INTO prompts VALUES ('p1', 'u-viva', 'a')")

    await _gdpr_orphan_resources_sqlite(conn)

    assert await _ids(conn, "prompts") == ["u-viva"]
    await conn.close()


async def test_conserva_el_catálogo_público_y_el_propietario_legacy(tmp_path):
    """`__public__` y `admin` no son cuentas y nunca están en `users`."""
    conn = await _db(tmp_path)
    await conn.execute("INSERT INTO prompts VALUES ('p1', '__public__', 'a')")
    await conn.execute("INSERT INTO memory_files VALUES ('m1', 'admin')")

    await _gdpr_orphan_resources_sqlite(conn)

    assert await _ids(conn, "prompts") == ["__public__"]
    assert await _ids(conn, "memory_files") == ["admin"]
    await conn.close()


async def test_una_instalación_sin_usuarios_no_se_vacía(tmp_path):
    """Sin filas en `users`, "no está en users" es cierto para todo."""
    conn = await _db(tmp_path, con_usuarios=False)
    await conn.execute("INSERT INTO prompts VALUES ('p1', 'cualquiera', 'a')")

    await _gdpr_orphan_resources_sqlite(conn)

    assert await _ids(conn, "prompts") == ["cualquiera"]
    await conn.close()
