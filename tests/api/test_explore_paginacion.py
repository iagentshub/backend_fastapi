"""Estabilidad y coste de las páginas del catálogo público (mejora #16).

El punto 16 bajó `LIMIT/OFFSET` al SQL, pero dejó dos deudas en Explorar: un
orden sin desempate único —que con filas empatadas devuelve la misma en dos
páginas y se salta otra— y una consulta por fila para averiguar el pack de cada
documento, que además leía la columna `content` entera.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage

# Todas las filas comparten instante y estrellas: es el caso que produce una
# sincronización de contenido oficial, y el que rompe un ORDER BY sin desempate.
_MISMO_INSTANTE = "2026-02-01T00:00:00Z"


def _login(client, username: str) -> None:
    r = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@explore.test",
            "password": "pass1234",
        },
    )
    assert r.status_code == 200, r.text


async def _sembrar_catalogo_empatado(marca: str, cuantos: int) -> None:
    async with open_db() as conn:
        for i in range(cuantos):
            await conn.execute(
                "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
                "description,is_public,category,stars_count,updated_at) "
                "VALUES ('skill',?,'catalog-owner',?,'',1,'Coding',7,?)",
                (f"{marca}-{i:03d}", f"{marca} skill {i}", _MISMO_INSTANTE),
            )
        await conn.commit()


def _order_by(sql: str) -> str:
    return sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]


def test_el_catalogo_y_el_feed_ordenan_por_una_clave_unica(client, monkeypatch):
    """El desempate se comprueba sobre el SQL, no sobre el resultado.

    SQLite devuelve un orden estable de facto aunque el `ORDER BY` no lo
    garantice, así que una prueba sobre las filas pasaría también con el bug y
    solo fallaría en PostgreSQL, donde el plan puede cambiar con el tamaño de
    la tabla. Lo que se fija aquí es que el orden termine en la clave primaria
    (`resource_type`, `resource_id`, `owner`), que es lo que hace la página
    reproducible en cualquier motor.
    """

    _login(client, f"ordensql{uuid4().hex[:6]}")
    from app.storage import db as db_module

    consultas: list[str] = []
    original = db_module.AsyncConn.fetchall

    async def espiar(self, sql, params=None):  # type: ignore[no-untyped-def]
        consultas.append(sql)
        return await original(self, sql, params)

    monkeypatch.setattr(db_module.AsyncConn, "fetchall", espiar)

    assert client.get("/api/v2/explore", params={"limit": 5}).status_code == 200
    assert client.get("/api/v2/feed", params={"limit": 5}).status_code == 200

    paginadas = [sql for sql in consultas if "FROM resource_social" in sql]
    assert paginadas, "no se capturó ninguna consulta paginada del catálogo"
    for sql in paginadas:
        orden = _order_by(sql)
        assert "resource_id" in orden and "owner" in orden, sql


def test_cursor_v2_no_repite_ni_pierde_filas_empatadas(client, monkeypatch):
    _login(client, f"explorepag{uuid4().hex[:6]}")
    marca = f"empate{uuid4().hex[:6]}"
    asyncio.run(_sembrar_catalogo_empatado(marca, 12))

    from app.storage import db as db_module

    consultas: list[str] = []
    original = db_module.AsyncConn.fetchall

    async def espiar(self, sql, params=None):  # type: ignore[no-untyped-def]
        consultas.append(sql)
        return await original(self, sql, params)

    monkeypatch.setattr(db_module.AsyncConn, "fetchall", espiar)
    vistos: list[str] = []
    cursor = None
    while True:
        r = client.get(
            "/api/v2/explore",
            params={"q": marca, "limit": 4, "cursor": cursor}
            if cursor
            else {"q": marca, "limit": 4},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        vistos.extend(item["resource_id"] for item in payload["items"])
        if not payload["page"]["has_more"]:
            break
        cursor = payload["page"]["next_cursor"]
        assert cursor

    assert len(vistos) == 12
    assert len(set(vistos)) == 12, "una fila apareció en dos páginas"
    selects = [sql for sql in consultas if "FROM resource_social" in sql]
    assert selects
    assert all(" OFFSET " not in sql.upper() for sql in selects)


def test_cursor_v2_firma_los_filtros(client):
    _login(client, f"explorectx{uuid4().hex[:6]}")
    marca = f"contexto{uuid4().hex[:6]}"
    asyncio.run(_sembrar_catalogo_empatado(marca, 3))
    first = client.get(
        "/api/v2/explore", params={"q": marca, "type": "skill", "limit": 1}
    ).json()
    cursor = first["page"]["next_cursor"]
    assert cursor
    changed = client.get(
        "/api/v2/explore",
        params={"q": marca, "type": "agent", "limit": 1, "cursor": cursor},
    )
    assert changed.status_code == 422
    assert changed.json()["detail"]["code"] == "invalid_cursor"


def test_cursor_v2_limita_filtros_repetidos(client):
    _login(client, f"explorelimits{uuid4().hex[:6]}")
    response = client.get(
        "/api/v2/explore",
        params=[("label", f"label-{index}") for index in range(21)],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "label"


def test_el_feed_pagina_sin_repetir_filas_empatadas(client):
    seguidor = f"feedpag{uuid4().hex[:6]}"
    seguido = f"feedautor{uuid4().hex[:6]}"
    _login(client, seguido)
    _login(client, seguidor)
    marca = f"feed{uuid4().hex[:6]}"

    async def sembrar() -> None:
        async with open_db() as conn:
            autor = await conn.fetchval(
                "SELECT id FROM users WHERE username = ?", (seguido,)
            )
            lector = await conn.fetchval(
                "SELECT id FROM users WHERE username = ?", (seguidor,)
            )
            await conn.execute(
                "INSERT INTO user_follows (follower, following) VALUES (?, ?)",
                (lector, autor),
            )
            for i in range(6):
                await conn.execute(
                    "INSERT INTO resource_social (resource_type,resource_id,owner,"
                    "name,description,is_public,category,stars_count,updated_at) "
                    "VALUES ('skill',?,?,?,'',1,'Coding',0,?)",
                    (f"{marca}-{i:03d}", autor, f"{marca} {i}", _MISMO_INSTANTE),
                )
            await conn.commit()

    asyncio.run(sembrar())

    vistos: list[str] = []
    cursor = None
    for _ in range(2):
        params = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        r = client.get("/api/v2/feed", params=params)
        assert r.status_code == 200, r.text
        payload = r.json()
        vistos.extend(item["resource_id"] for item in payload["items"])
        cursor = payload["page"]["next_cursor"]

    assert len(set(vistos)) == len(vistos) == 6


def test_el_catalogo_resuelve_los_packs_de_la_pagina_en_una_consulta(
    client, monkeypatch
):
    """La procedencia de N documentos no puede costar N consultas."""

    _login(client, f"packn1{uuid4().hex[:6]}")
    marca = f"docs{uuid4().hex[:6]}"

    async def sembrar() -> None:
        async with open_db() as conn:
            for i in range(8):
                await conn.execute(
                    "INSERT INTO resource_social (resource_type,resource_id,owner,"
                    "name,description,is_public,category,stars_count,updated_at) "
                    "VALUES ('knowledge',?,'catalog-owner',?,'',1,'Other',0,?)",
                    (f"{marca}-{i:03d}", f"{marca} doc {i}", _MISMO_INSTANTE),
                )
            await conn.commit()

    asyncio.run(sembrar())

    from app.storage import db as db_module

    consultas: list[str] = []
    original = db_module.AsyncConn.fetchall
    original_one = db_module.AsyncConn.fetchone

    async def espiar_all(self, sql, params=None):  # type: ignore[no-untyped-def]
        consultas.append(sql)
        return await original(self, sql, params)

    async def espiar_one(self, sql, params=None):  # type: ignore[no-untyped-def]
        consultas.append(sql)
        return await original_one(self, sql, params)

    monkeypatch.setattr(db_module.AsyncConn, "fetchall", espiar_all)
    monkeypatch.setattr(db_module.AsyncConn, "fetchone", espiar_one)

    r = client.get("/api/v2/explore", params={"q": marca, "limit": 8})
    assert r.status_code == 200
    assert len(r.json()["items"]) == 8

    sobre_knowledge = [sql for sql in consultas if "FROM knowledge_items" in sql]
    assert len(sobre_knowledge) <= 1, sobre_knowledge
    # Y esa consulta no debe arrastrar el contenido de los documentos.
    for sql in sobre_knowledge:
        assert "content" not in sql, sql


def test_pack_locations_resuelve_la_pagina_en_una_consulta(client):
    """La procedencia de N documentos no puede costar N consultas."""

    _login(client, f"packloc{uuid4().hex[:6]}")
    storage = KnowledgeStorage()

    async def comprobar() -> None:
        ids: list[str] = []
        for i in range(5):
            item = await storage.save(
                type="text",
                title=f"Documento {i}",
                source="test",
                content="contenido irrelevante " * 50,
                owner_id="packowner",
            )
            ids.append(str(item["id"]))
        async with open_db() as conn:
            for posicion, item_id in enumerate(ids[:3]):
                await conn.execute(
                    "UPDATE knowledge_items SET pack_id=?, pack_relative_path=? "
                    "WHERE id=?",
                    ("pack-1", f"docs/{posicion}.md", item_id),
                )
            await conn.commit()

        ubicaciones = await storage.pack_locations(ids + ["inexistente", ""])
        assert set(ubicaciones) == set(ids[:3])
        assert ubicaciones[ids[0]] == {
            "pack_id": "pack-1",
            "pack_relative_path": "docs/0.md",
        }
        # Sin ids no debe tocar la base de datos.
        assert await storage.pack_locations([]) == {}

    asyncio.run(comprobar())
