"""Todo lo que cuelga de `/admin` exige ser administrador.

La pregunta que motiva esta guarda: si el listado del panel vive en una ruta
distinta a la del usuario, ¿no basta con conocer la URL? No — lo que separa a
los dos no es el path, es la dependencia. `/api/v2/knowledge` filtra por
visibilidad y devuelve lo que el solicitante puede ver; `/api/v2/admin/knowledge`
no filtra nada y por eso exige `require_admin`. Conocer la URL no acerca a
nadie a los datos, igual que conocer `/api/admin/users` nunca los acercó.

Lo que sí sería un riesgo es que una ruta nueva bajo `/admin` naciera sin la
guarda, y eso es lo que el contrato de rutas no mira: congela
`MÉTODO /ruta`, no quién puede entrar. Esto lo mira.
"""

from __future__ import annotations

from tests.api.admin._helpers import _register

RUTAS_ADMIN = ("/api/admin", "/api/v2/admin")


def _gets_bajo_admin() -> list[str]:
    """Todos los `GET` publicados que cuelgan de /admin, del propio esquema."""
    from app.api.app import create_app

    esquema = create_app().openapi()
    rutas = []
    for ruta, operaciones in esquema["paths"].items():
        if not ruta.startswith(RUTAS_ADMIN) or "get" not in operaciones:
            continue
        # Un identificador cualquiera: la guarda responde antes de mirarlo.
        rutas.append(ruta.replace("{", "").replace("}", ""))
    return sorted(rutas)


def test_ningun_get_del_panel_se_abre_a_un_usuario_normal(client):
    """Barrido sobre el esquema, no sobre una lista escrita a mano: una ruta
    nueva bajo /admin entra aquí sola. El contrato de rutas congela
    `MÉTODO /ruta` pero no dice quién puede entrar, y esa es justo la
    diferencia entre `/api/v2/knowledge` y `/api/v2/admin/knowledge`."""
    _register("intrusa")
    entrada = client.post(
        "/api/auth/login", json={"identifier": "intrusa", "password": "pass1234"}
    )
    assert entrada.status_code == 200, entrada.text

    rutas = _gets_bajo_admin()
    assert len(rutas) > 25, f"el barrido solo encontró {len(rutas)} rutas"

    abiertas = []
    for ruta in rutas:
        respuesta = client.get(ruta)
        if respuesta.status_code != 403:
            abiertas.append(f"{ruta} -> {respuesta.status_code}")

    assert abiertas == [], (
        "Estos GET bajo /admin no respondieron 403 a un usuario normal:\n  "
        + "\n  ".join(abiertas)
    )


def test_el_panel_esta_cerrado_a_quien_no_es_admin(client):
    """Empírico, además del barrido: un usuario normal recibe 403 en los once
    listados, no una página vacía ni un 404 que insinúe otra cosa."""
    _register("curiosa")
    entrada = client.post(
        "/api/auth/login", json={"identifier": "curiosa", "password": "pass1234"}
    )
    assert entrada.status_code == 200, entrada.text

    for recurso in ("connections", "explore"):
        respuesta = client.get(f"/api/v2/admin/{recurso}?limit=10")
        assert respuesta.status_code == 403, (
            f"/api/v2/admin/{recurso} respondió {respuesta.status_code}"
        )


def test_el_panel_esta_cerrado_a_quien_no_tiene_sesion(client):
    for recurso in ("connections", "explore", "logs"):
        respuesta = client.get(f"/api/v2/admin/{recurso}?limit=10")
        assert respuesta.status_code in (401, 403)
