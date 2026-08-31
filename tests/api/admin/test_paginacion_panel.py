"""El panel dejó de traerse la instalación entera en cada pestaña.

Once `GET` de `/api/admin` devolvían `SELECT … FROM tabla` sin `WHERE` y sin
cota, y resolvían el nombre del dueño con la tabla `users` completa. Se
retiraron: el inventario del panel se pide por `/api/v2/admin/explore`, que ya
pagina, y de los listados por tipo solo sobrevive el de conexiones, que es el
único con consumidor.
"""

from __future__ import annotations

from tests.api.admin._helpers import _AGENT_PAYLOAD, _insert_connection, _register

V2 = "/api/v2/admin"

# Los once que se retiraron. Su vuelta sería la vuelta del listado sin cota.
RETIRADOS = (
    "agents", "skills", "prompts", "tools", "memory", "knowledge",
    "workflows", "groups", "users", "llm-orchestrations",
)


def _crear_agentes(client, cuantos: int) -> None:
    for indice in range(cuantos):
        payload = dict(_AGENT_PAYLOAD)
        payload["name"] = f"Agente {indice:03d}"
        client.post("/api/agents", json=payload)


def test_los_listados_sin_cota_ya_no_existen(admin_client):
    for recurso in RETIRADOS:
        respuesta = admin_client.get(f"/api/admin/{recurso}")
        assert respuesta.status_code in (404, 405), (
            f"/api/admin/{recurso} sigue publicado: es un SELECT sin cota"
        )


def test_el_listado_de_conexiones_respeta_el_limite(admin_client):
    for indice in range(5):
        _insert_connection(f"conexion-{indice}")
    r = admin_client.get(f"{V2}/connections?limit=2")
    assert r.status_code == 200
    cuerpo = r.json()
    assert len(cuerpo["items"]) == 2
    assert cuerpo["page"]["limit"] == 2
    assert cuerpo["page"]["has_more"] is True
    assert cuerpo["page"]["next_cursor"]


def test_el_cursor_recorre_sin_repetir_ni_saltar(admin_client):
    for indice in range(7):
        _insert_connection(f"recorrido-{indice}")
    vistos: list[str] = []
    cursor = None
    for _ in range(10):
        url = f"{V2}/connections?limit=3" + (f"&cursor={cursor}" if cursor else "")
        cuerpo = admin_client.get(url).json()
        vistos.extend(item["id"] for item in cuerpo["items"])
        cursor = cuerpo["page"]["next_cursor"]
        if not cursor:
            break
    assert len(vistos) == len(set(vistos)), "el recorrido repitió filas"
    completo = admin_client.get(f"{V2}/connections?limit=100").json()["items"]
    assert set(vistos) == {item["id"] for item in completo}


def test_el_nombre_del_dueno_sale_del_join(admin_client):
    """Antes lo resolvía `_username_map`, la tabla `users` entera, llamada
    nueve veces por carga del panel."""
    _insert_connection("con-dueño")
    items = admin_client.get(f"{V2}/connections?limit=50").json()["items"]
    assert items
    assert all("owner_username" in item for item in items)


def test_el_total_exacto_es_opcional(admin_client):
    for indice in range(3):
        _insert_connection(f"total-{indice}")
    sin_total = admin_client.get(f"{V2}/connections?limit=2").json()
    assert sin_total["page"]["total"] is None
    con_total = admin_client.get(f"{V2}/connections?limit=2&include_total=true").json()
    assert con_total["page"]["total"] >= 3


def test_offset_sigue_rechazado_en_el_panel(admin_client):
    r = admin_client.get(f"{V2}/connections?offset=10")
    assert r.status_code == 422
    detalle = r.json()["detail"]
    assert detalle["code"] == "invalid_field"
    assert detalle["field"] == "offset"


def test_el_panel_no_se_abre_sin_ser_admin(client):
    _register("curioso")
    client.post(
        "/api/auth/login", json={"identifier": "curioso", "password": "pass1234"}
    )
    assert client.get(f"{V2}/connections").status_code in (401, 403)


def test_el_listado_no_proyecta_una_columna_de_contenido():
    """Guarda estructural, no de comportamiento: el listado de memoria traía
    `content` entero para hacerle `len()`. Se mira la proyección, que es donde
    se decide qué cruza el cable."""
    import re

    from app.services.admin_connection_listing import _COLUMNS

    proyeccion = _COLUMNS.lower()
    sin_length = re.sub(r"length\s*\([^)]*\)", "", proyeccion)
    assert not re.search(r"(?:select|,|\.)\s*content\s*(?:,|$| )", sin_length)


def test_las_estadisticas_cuentan_los_agentes_de_la_tabla(admin_client):
    """Se contaban recorriendo AGENTS_DIR/{public,private}/*/config.json —los
    ficheros que dejó la migración a base de datos—, así que en cualquier
    instalación creada después el panel decía cero agentes sin que nada
    fallara. La base de este test nace vacía: es exactamente ese caso."""
    _crear_agentes(admin_client, 3)
    stats = admin_client.get("/api/admin/stats").json()
    assert stats["agents_private"] == 3
    assert stats["agents_public"] == 0


def test_el_inventario_sirve_lo_que_la_tarjeta_de_grupo_pinta(admin_client):
    """La tarjeta del panel lee `member_count`, `agents_count`, `status`… y el
    inventario nunca los sirvió: los daba el listado por tipo, que el panel
    dejó de usar. Salían todos a cero sin que nada fallara."""
    grupo = admin_client.post("/api/groups", json={"name": "Equipo"}).json()
    cambio = admin_client.post(f"/api/groups/switch/{grupo['id']}")
    assert cambio.status_code == 200, cambio.text
    _crear_agentes(admin_client, 2)

    items = admin_client.get(f"{V2}/explore?type=group&limit=50").json()["items"]
    suyo = next(g for g in items if g["id"] == grupo["id"])
    assert suyo["agents_count"] == 2
    assert suyo["member_count"] >= 1
    assert suyo["status"] == "active"
    for campo in ("connections_count", "knowledge_count", "tokens_in", "tokens_out"):
        assert campo in suyo, f"la tarjeta pinta '{campo}' y el inventario no lo trae"


def test_el_inventario_no_transporta_la_memoria_para_medirla(admin_client):
    """El `len(content)` que este punto vino a quitar seguía aquí, que es el
    listado que el panel sí usa."""
    contenido = "x" * 5000
    guardado = admin_client.post("/api/memory/notas", json={"content": contenido})
    assert guardado.status_code == 200, guardado.text

    items = admin_client.get(f"{V2}/explore?type=memory&limit=50").json()["items"]
    fichero = next(i for i in items if i["filename"] == "notas")
    assert fichero["size"] == len(contenido)
    assert "content" not in fichero

    from app.services.admin_explore_hydration import _proyeccion

    proyeccion = _proyeccion("memory", "memory_files").lower()
    assert "length(content)" in proyeccion
    assert "*" not in proyeccion


def test_el_inventario_publica_la_url_de_la_foto(admin_client):
    """`avatar_url` no es una columna: la foto vive en `user_avatars`. Sin el
    JOIN el panel de personas pinta la inicial y nunca la foto."""
    items = admin_client.get(f"{V2}/explore?type=user&limit=50").json()["items"]
    assert items
    for usuario in items:
        assert "avatar_url" in usuario
        assert "password_hash" not in usuario
