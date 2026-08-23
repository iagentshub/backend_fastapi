"""El catálogo separa lo que aún no tienes de lo que ya enlazaste.

Explorar mostraba también los recursos de los que el usuario ya tenía una copia
enlazada, con el botón de enlazar activo: la única señal de "ya lo tengo" vivía
en memoria del cliente y se perdía al recargar. `relation` mueve la decisión al
servidor, que es donde está la paginación — filtrar en cliente dejaba el total
de la cabecera mintiendo y páginas a medio llenar.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.storage.db import open_db

_INSTANTE = "2026-03-01T00:00:00Z"


def _registrar(client, username: str) -> None:
    r = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@relacion.test",
            "password": "pass1234",
        },
    )
    assert r.status_code == 200, r.text


async def _id_de(username: str) -> str:
    async with open_db() as conn:
        return await conn.fetchval(
            "SELECT id FROM users WHERE username = ?", (username,)
        )


async def _publicar(marca: str, resource_id: str, owner: str) -> None:
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
            "description,is_public,category,stars_count,updated_at) "
            "VALUES ('skill',?,?,?,'',1,'Coding',0,?)",
            (resource_id, owner, f"{marca} {resource_id}", _INSTANTE),
        )
        await conn.commit()


async def _enlazar(marca: str, origen_id: str, origen_owner: str, mio: str) -> None:
    """Copia privada del que enlaza, apuntando al original: lo que hace link_*."""
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
            "description,is_public,category,stars_count,linked_to_user,"
            "linked_to_id,updated_at) "
            "VALUES ('skill',?,?,?,'',0,'Coding',0,?,?,?)",
            (
                f"copia-{origen_id}",
                mio,
                f"{marca} copia",
                origen_owner,
                origen_id,
                _INSTANTE,
            ),
        )
        await conn.commit()


def _ids(respuesta) -> set[str]:
    assert respuesta.status_code == 200, respuesta.text
    return {item["resource_id"] for item in respuesta.json()}


def _preparar(client) -> tuple[str, str, str]:
    """Publica dos recursos ajenos y enlaza uno. Devuelve marca, nuevo y tenido."""
    usuario = f"relacion{uuid4().hex[:6]}"
    _registrar(client, usuario)
    marca = f"rel{uuid4().hex[:6]}"
    nuevo, tenido = f"{marca}-nuevo", f"{marca}-tenido"

    async def sembrar() -> None:
        yo = await _id_de(usuario)
        await _publicar(marca, nuevo, "otro-owner")
        await _publicar(marca, tenido, "otro-owner")
        await _enlazar(marca, tenido, "otro-owner", yo)

    asyncio.run(sembrar())
    return marca, nuevo, tenido


def test_relacion_new_esconde_lo_que_ya_esta_enlazado(client):
    marca, nuevo, tenido = _preparar(client)

    r = client.get("/api/explore", params={"q": marca, "relation": "new"})
    assert _ids(r) == {nuevo}


def test_relacion_linked_devuelve_solo_lo_enlazado(client):
    marca, nuevo, tenido = _preparar(client)

    r = client.get("/api/explore", params={"q": marca, "relation": "linked"})
    assert _ids(r) == {tenido}


def test_sin_relacion_el_catalogo_sigue_completo(client):
    """El valor por defecto no cambia: quien no envíe el parámetro ve todo."""
    marca, nuevo, tenido = _preparar(client)

    assert _ids(client.get("/api/explore", params={"q": marca})) == {nuevo, tenido}
    assert _ids(
        client.get("/api/explore", params={"q": marca, "relation": "all"})
    ) == {nuevo, tenido}


def test_cada_fila_dice_si_ya_esta_enlazada(client):
    """En `all` conviven las dos, así que la marca viaja por fila."""
    marca, nuevo, tenido = _preparar(client)

    r = client.get("/api/explore", params={"q": marca, "relation": "all"})
    assert r.status_code == 200, r.text
    estado = {item["resource_id"]: item["linked_by_me"] for item in r.json()}
    assert estado == {nuevo: False, tenido: True}


def test_el_total_de_la_cabecera_respeta_el_filtro(client):
    """Si el COUNT no aplicara la condición, "cargar más" pediría páginas vacías."""
    marca, _, _ = _preparar(client)

    r = client.get("/api/explore", params={"q": marca, "relation": "new", "limit": 1})
    assert r.status_code == 200, r.text
    assert r.headers["X-Total-Count"] == "1"
    # Queda algo que enseñar: el cliente no necesita explicar ningún vacío.
    assert "X-Linked-Count" not in r.headers


def test_un_vacio_por_el_filtro_dice_cuanto_dejo_fuera(client):
    """Sin este dato, buscar algo que ya tienes devuelve un vacío inexplicable."""
    usuario = f"relvacio{uuid4().hex[:6]}"
    _registrar(client, usuario)
    marca = f"rel{uuid4().hex[:6]}"

    async def sembrar() -> None:
        yo = await _id_de(usuario)
        for i in range(3):
            await _publicar(marca, f"{marca}-{i}", "otro-owner")
            await _enlazar(marca, f"{marca}-{i}", "otro-owner", yo)

    asyncio.run(sembrar())

    r = client.get("/api/explore", params={"q": marca, "relation": "new"})
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert r.headers["X-Linked-Count"] == "3"


def test_la_cabecera_solo_aparece_en_el_modo_que_esconde_cosas(client):
    """En `all` y `linked` nada queda fuera, así que no hay nada que explicar."""
    marca, _, _ = _preparar(client)
    sin_resultados = {"q": f"{marca}-inexistente"}

    for modo in ("all", "linked"):
        r = client.get("/api/explore", params={**sin_resultados, "relation": modo})
        assert r.status_code == 200, r.text
        assert "X-Linked-Count" not in r.headers


def test_la_copia_de_otro_usuario_no_marca_mi_catalogo(client):
    """El EXISTS filtra por `owner`: que otro lo tenga no me lo esconde a mí."""
    usuario = f"relmio{uuid4().hex[:6]}"
    ajeno = f"relajeno{uuid4().hex[:6]}"
    _registrar(client, ajeno)
    _registrar(client, usuario)
    marca = f"rel{uuid4().hex[:6]}"
    original = f"{marca}-original"

    async def sembrar() -> None:
        otro = await _id_de(ajeno)
        await _publicar(marca, original, "otro-owner")
        await _enlazar(marca, original, "otro-owner", otro)

    asyncio.run(sembrar())

    assert _ids(client.get("/api/explore", params={"q": marca, "relation": "new"})) == {
        original
    }
    assert (
        _ids(client.get("/api/explore", params={"q": marca, "relation": "linked"}))
        == set()
    )


async def _sembrar_pack(source_id: str, marca: str, componentes: int) -> None:
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO official_sources (id,name,description,repository_url,"
            "repository_owner,repository_name,created_at,updated_at) "
            "VALUES (?,?,'',?,'iagentshub',?,?,?)",
            (source_id, f"{marca} pack", f"https://x.test/{source_id}",
             source_id, _INSTANTE, _INSTANTE),
        )
        for i in range(componentes):
            resource_id = f"{source_id}-c{i}"
            await conn.execute(
                "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
                "description,is_public,category,stars_count,labels,updated_at) "
                "VALUES ('skill',?,'pack-owner',?,'',1,'Coding',0,?,?)",
                (resource_id, f"{marca} c{i}", '["official"]', _INSTANTE),
            )
            await conn.execute(
                "INSERT INTO resource_source_links (source_id,component_key,"
                "resource_type,resource_id,resource_owner_id,created_at,updated_at) "
                "VALUES (?,?,'skill',?,'pack-owner',?,?)",
                (source_id, f"skill:{i}", resource_id, _INSTANTE, _INSTANTE),
            )
        await conn.commit()


async def _enlazar_componentes(source_id: str, indices: list[int], mio: str) -> None:
    async with open_db() as conn:
        for i in indices:
            await conn.execute(
                "INSERT INTO resource_social (resource_type,resource_id,owner,name,"
                "description,is_public,category,stars_count,linked_to_user,"
                "linked_to_id,updated_at) "
                "VALUES ('skill',?,?,'copia','',0,'Coding',0,'pack-owner',?,?)",
                (f"copia-{source_id}-c{i}", mio, f"{source_id}-c{i}", _INSTANTE),
            )
        await conn.commit()


def test_un_pack_a_medias_sigue_siendo_descubrible(client):
    """`complete` sale del catálogo; `partial` no, porque aún queda algo nuevo.

    Tratar el pack como un booleano escondería los componentes que el usuario
    todavía no ha enlazado.
    """
    usuario = f"relpack{uuid4().hex[:6]}"
    _registrar(client, usuario)
    marca = f"pk{uuid4().hex[:6]}"
    intacto, medias, entero = f"{marca}-n", f"{marca}-p", f"{marca}-c"

    async def sembrar() -> None:
        yo = await _id_de(usuario)
        await _sembrar_pack(intacto, marca, 2)
        await _sembrar_pack(medias, marca, 3)
        await _sembrar_pack(entero, marca, 2)
        await _enlazar_componentes(medias, [0], yo)
        await _enlazar_componentes(entero, [0, 1], yo)

    asyncio.run(sembrar())

    def packs(relation: str) -> dict[str, str]:
        r = client.get(
            "/api/explore/official-packs", params={"q": marca, "relation": relation}
        )
        assert r.status_code == 200, r.text
        return {item["source_id"]: item["link_state"] for item in r.json()}

    assert packs("all") == {intacto: "none", medias: "partial", entero: "complete"}
    assert packs("new") == {intacto: "none", medias: "partial"}
    assert packs("linked") == {medias: "partial", entero: "complete"}


def test_un_modo_de_relacion_desconocido_es_422(client):
    _registrar(client, f"relmala{uuid4().hex[:6]}")

    r = client.get("/api/explore", params={"relation": "mias"})
    assert r.status_code == 422, r.text
    detalle = r.json()["detail"]
    assert detalle["code"] == "invalid_field"
    assert detalle["field"] == "relation"

    r = client.get("/api/explore/official-packs", params={"relation": "mias"})
    assert r.status_code == 422, r.text
