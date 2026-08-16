"""El ciclo completo compartir → descompartir de un agente (mejora #45).

Compartir un agente arrastraba sus skills, su knowledge y sus prompts; retirarlo
no arrastraba nada. El agente desaparecía de la vista del grupo y el usuario daba
por revocado un acceso que seguía vivo: el residuo quedaba visible para todo el
grupo, sin nada en la interfaz que lo dijera.

La suite tenía 22 tests de la cascada al compartir y ninguno del camino inverso.
"""

from __future__ import annotations

from uuid import uuid4


def _register(username: str) -> None:
    import asyncio

    from app.auth.auth import register_user

    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass


def _set_cookie(client, username: str) -> None:
    from app.auth.auth import create_token

    client.cookies.set("ga_token", create_token(username))


def _nombre(prefijo: str) -> str:
    return f"{prefijo}{uuid4().hex[:8]}"


def _agente_con_dependencias(client, titulo: str) -> dict:
    """Agente con una skill, un prompt y un documento propios."""
    skill = client.post(
        "/api/skills/private", json={"name": f"Skill {titulo}", "description": "d"}
    ).json()
    prompt = client.post(
        "/api/prompts/private",
        json={"name": f"Prompt {titulo}", "alias": _nombre("al"), "content": "c"},
    ).json()
    know = client.post(
        "/api/knowledge/text", json={"title": f"Doc {titulo}", "content": "contenido"}
    ).json()
    agent = client.post(
        "/api/agents",
        json={
            "name": f"Agente {titulo}",
            "system_prompt": "p",
            "model": "gpt-4o",
            "skills": [skill["id"]],
            "prompts": [prompt["id"]],
            "knowledge": [know["id"]],
        },
    ).json()
    return {"agent": agent, "skill": skill, "prompt": prompt, "knowledge": know}


def _ids_compartidos(client, group_id: str, resource_type: str) -> set[str]:
    import asyncio

    from app.storage.group_shares import GroupShareStorage

    return set(
        asyncio.run(
            GroupShareStorage().get_group_shared_resource_ids(group_id, resource_type)
        )
    )


def test_descompartir_un_agente_retira_lo_que_arrastro(client):
    usuario = _nombre("casc_owner_")
    _register(usuario)
    _set_cookie(client, usuario)
    grupo = client.post("/api/groups", json={"name": "Grupo cascada"}).json()
    recursos = _agente_con_dependencias(client, "cascada")
    agente_id = recursos["agent"]["id"]

    client.post(f"/api/sharing/agent/{agente_id}", json={"group_id": grupo["id"]})
    assert recursos["skill"]["id"] in _ids_compartidos(client, grupo["id"], "skill")

    respuesta = client.delete(
        f"/api/sharing/agent/{agente_id}", params={"group_id": grupo["id"]}
    )
    assert respuesta.status_code == 200
    retirados = set(respuesta.json()["uncascaded"])
    assert retirados == {
        recursos["skill"]["id"],
        recursos["prompt"]["id"],
        recursos["knowledge"]["id"],
    }

    for tipo, clave in (
        ("agent", "agent"),
        ("skill", "skill"),
        ("prompt", "prompt"),
        ("knowledge", "knowledge"),
    ):
        assert recursos[clave]["id"] not in _ids_compartidos(
            client, grupo["id"], tipo
        ), f"quedó acceso residual a {tipo}"


def test_el_miembro_deja_de_ver_las_dependencias(client):
    """La comprobación que importa es la del otro lado: qué ve el grupo."""

    duenno = _nombre("casc_own2_")
    miembro = _nombre("casc_mem2_")
    _register(duenno)
    _register(miembro)
    _set_cookie(client, duenno)
    grupo = client.post("/api/groups", json={"name": "Grupo visible"}).json()
    client.post(
        f"/api/groups/{grupo['id']}/members",
        json={"username": miembro, "role": "member"},
    )
    recursos = _agente_con_dependencias(client, "visible")
    client.post(
        f"/api/sharing/agent/{recursos['agent']['id']}", json={"group_id": grupo["id"]}
    )

    _set_cookie(client, miembro)
    assert any(
        s["id"] == recursos["skill"]["id"] for s in client.get("/api/skills").json()
    )
    assert any(
        k["id"] == recursos["knowledge"]["id"]
        for k in client.get("/api/knowledge").json()
    )

    _set_cookie(client, duenno)
    client.delete(
        f"/api/sharing/agent/{recursos['agent']['id']}",
        params={"group_id": grupo["id"]},
    )

    _set_cookie(client, miembro)
    assert not any(
        s["id"] == recursos["skill"]["id"] for s in client.get("/api/skills").json()
    )
    assert not any(
        p["id"] == recursos["prompt"]["id"] for p in client.get("/api/prompts").json()
    )
    assert not any(
        k["id"] == recursos["knowledge"]["id"]
        for k in client.get("/api/knowledge").json()
    )


def test_conserva_lo_que_otro_agente_compartido_sigue_usando(client):
    """Retirar la skill dejaría sin ella al otro agente que el grupo sí usa."""

    usuario = _nombre("casc_share_")
    _register(usuario)
    _set_cookie(client, usuario)
    grupo = client.post("/api/groups", json={"name": "Grupo compartido"}).json()
    recursos = _agente_con_dependencias(client, "primero")
    segundo = client.post(
        "/api/agents",
        json={
            "name": "Agente que también la usa",
            "system_prompt": "p",
            "model": "gpt-4o",
            "skills": [recursos["skill"]["id"]],
        },
    ).json()

    client.post(
        f"/api/sharing/agent/{recursos['agent']['id']}", json={"group_id": grupo["id"]}
    )
    client.post(f"/api/sharing/agent/{segundo['id']}", json={"group_id": grupo["id"]})

    respuesta = client.delete(
        f"/api/sharing/agent/{recursos['agent']['id']}",
        params={"group_id": grupo["id"]},
    )
    cuerpo = respuesta.json()

    assert recursos["skill"]["id"] in cuerpo["kept"]
    assert recursos["skill"]["id"] in _ids_compartidos(client, grupo["id"], "skill")
    # El prompt y el documento solo los usaba el agente retirado.
    assert recursos["prompt"]["id"] in cuerpo["uncascaded"]
    assert recursos["knowledge"]["id"] in cuerpo["uncascaded"]


def test_no_retira_lo_que_el_usuario_compartio_por_su_cuenta(client):
    """Compartir a mano no es una dependencia: sobrevive a la marcha del agente."""

    usuario = _nombre("casc_expl_")
    _register(usuario)
    _set_cookie(client, usuario)
    grupo = client.post("/api/groups", json={"name": "Grupo explícito"}).json()
    recursos = _agente_con_dependencias(client, "explicito")

    # Primero la skill por su cuenta, después el agente que la usa.
    client.post(
        f"/api/sharing/skill/{recursos['skill']['id']}", json={"group_id": grupo["id"]}
    )
    client.post(
        f"/api/sharing/agent/{recursos['agent']['id']}", json={"group_id": grupo["id"]}
    )

    respuesta = client.delete(
        f"/api/sharing/agent/{recursos['agent']['id']}",
        params={"group_id": grupo["id"]},
    )

    assert recursos["skill"]["id"] not in respuesta.json()["uncascaded"]
    assert recursos["skill"]["id"] in _ids_compartidos(client, grupo["id"], "skill")
    # Lo que sí llegó con el agente se va con él.
    assert recursos["knowledge"]["id"] in respuesta.json()["uncascaded"]


def test_descompartir_una_orquestacion_retira_sus_agentes_y_dependencias(client):
    usuario = _nombre("casc_wf_")
    _register(usuario)
    _set_cookie(client, usuario)
    grupo = client.post("/api/groups", json={"name": "Grupo workflow"}).json()
    recursos = _agente_con_dependencias(client, "workflow")
    agente_id = recursos["agent"]["id"]

    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Orquestación",
            "description": "d",
            "definition": {
                "nodes": [{"id": "n1", "agent_id": agente_id, "label": "paso"}],
                "edges": [],
            },
        },
    ).json()

    client.post(f"/api/sharing/workflow/{workflow['id']}", json={"group_id": grupo["id"]})
    assert agente_id in _ids_compartidos(client, grupo["id"], "agent")

    respuesta = client.delete(
        f"/api/sharing/workflow/{workflow['id']}", params={"group_id": grupo["id"]}
    )
    retirados = set(respuesta.json()["uncascaded"])

    assert agente_id in retirados
    assert recursos["skill"]["id"] in retirados
    assert agente_id not in _ids_compartidos(client, grupo["id"], "agent")
    assert recursos["skill"]["id"] not in _ids_compartidos(client, grupo["id"], "skill")
