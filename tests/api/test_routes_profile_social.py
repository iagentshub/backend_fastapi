"""Tests fase 1 — perfil social: PUT /me/profile, POST /me/avatar, GET /users/{username}."""

from __future__ import annotations

import io


def _png_bytes() -> bytes:
    """PNG de 1x1 válido — detect_avatar_mime lo exige."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _register_and_login(client, username="socialuser", password="pass1234"):
    import asyncio

    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def test_actualizar_perfil_campos_basicos(client):
    _register_and_login(client)
    r = client.put(
        "/api/auth/me/profile",
        json={
            "bio": "Desarrollador de agentes IA",
            "languages": ["es", "en"],
            "is_email_public": True,
            "github": "https://github.com/myghuser",
            "cv": "# Mi CV\n\nExperiencia en Python.",
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    profile = client.get("/api/users/socialuser").json()
    assert profile["email_public"] == "socialuser@example.com"


def test_perfil_publico_devuelve_campos(client):
    _register_and_login(client, "perfiluser")
    client.put(
        "/api/auth/me/profile",
        json={
            "bio": "Bio pública",
            "languages": ["es"],
            "github": "https://github.com/perfilgh",
        },
    )
    r = client.get("/api/users/perfiluser")
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "perfiluser"
    assert data["bio"] == "Bio pública"
    assert data["languages"] == ["es"]
    assert data["github"] == "https://github.com/perfilgh"
    assert "joined_at" in data


def test_perfil_publico_requiere_auth(client):
    import asyncio

    from app.auth.auth import register_user

    asyncio.run(register_user("targetuser", "pass1234", email="target@example.com"))
    r = client.get("/api/users/targetuser")
    assert r.status_code == 401


def test_perfil_publico_usuario_inexistente(client):
    _register_and_login(client, "looker")
    r = client.get("/api/users/noexiste_xyz")
    assert r.status_code == 404


def test_campos_opcionales_vacios(client):
    _register_and_login(client, "emptyprofile")
    r = client.get("/api/users/emptyprofile")
    assert r.status_code == 200
    data = r.json()
    assert data["bio"] is None
    assert data["github"] is None
    assert data["email_public"] is None
    assert data["cv"] is None
    assert data["avatar_url"] is None
    assert data["languages"] == []


def test_github_url_invalida_rechazada(client):
    """N3: el campo github solo acepta URLs https://."""
    _register_and_login(client, "ghvalidation")
    # javascript: URI debe ser rechazado con 422
    r = client.put("/api/auth/me/profile", json={"github": "javascript:alert(1)"})
    assert r.status_code == 422
    # URL http:// también rechazada
    r2 = client.put("/api/auth/me/profile", json={"github": "http://github.com/user"})
    assert r2.status_code == 422
    # Valor vacío permitido
    r3 = client.put("/api/auth/me/profile", json={"github": ""})
    assert r3.status_code == 200
    # URL https:// válida
    r4 = client.put("/api/auth/me/profile", json={"github": "https://github.com/user"})
    assert r4.status_code == 200


def test_idiomas_invalidos_filtrados(client):
    _register_and_login(client, "languser")
    client.put(
        "/api/auth/me/profile", json={"languages": ["es", "xx", "klingon", "en"]}
    )
    r = client.get("/api/users/languser")
    assert r.status_code == 200
    langs = r.json()["languages"]
    assert "es" in langs
    assert "en" in langs
    assert "xx" not in langs
    assert "klingon" not in langs


def test_avatar_subida_y_lectura(client):
    _register_and_login(client, "avataruser")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("test.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert r.status_code == 200
    assert "/api/users/avataruser/avatar" in r.json()["avatar_url"]

    r2 = client.get("/api/users/avataruser/avatar")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "image/png"


def test_avatar_formato_no_permitido(client):
    _register_and_login(client, "badavataruser")
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
    )
    assert r.status_code == 400


def test_avatar_sin_fichero_devuelve_204(client):
    import asyncio

    from app.auth.auth import register_user

    asyncio.run(register_user("noavataruser", "pass1234", email="noavatar@example.com"))
    _register_and_login(client, "viewer2")
    r = client.get("/api/users/noavataruser/avatar")
    assert r.status_code == 204


def test_avatar_10mb_aceptado(client):
    _register_and_login(client, "avatarbig")
    data = b"\xff\xd8\xff" + b"x" * (9 * 1024 * 1024 - 3)
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("big.jpg", io.BytesIO(data), "image/jpeg")},
    )
    assert r.status_code == 200


def test_avatar_rechaza_extension_valida_con_contenido_arbitrario(client):
    _register_and_login(client, "avatarspoof")
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("fake.png", io.BytesIO(b"no es una imagen"), "image/png")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "avatar_format_not_allowed"


def test_avatar_webp_se_sirve_con_mime_correcto(client):
    _register_and_login(client, "avatarwebp")
    webp = b"RIFF\x04\x00\x00\x00WEBPVP8 "
    uploaded = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("avatar.webp", io.BytesIO(webp), "image/webp")},
    )
    assert uploaded.status_code == 200
    served = client.get("/api/users/avatarwebp/avatar")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"


def test_avatar_grande_pasa_si_el_admin_no_puso_limite(client):
    """Por defecto no hay techo, y el avatar no puede inventarse uno propio.

    Tenía uno de 10 MB, tercero de tres límites distintos para la misma
    subida: el middleware cortaba en 2 MB y nginx en 1, así que su mensaje
    —«no puede superar 10 MB»— nunca llegó a ser cierto.
    """
    _register_and_login(client, "avatartoobig")
    data = _png_bytes() + b"\x00" * (3 * 1024 * 1024)
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("grande.png", io.BytesIO(data), "image/png")},
    )
    assert r.status_code == 200, r.text


def test_avatar_por_encima_del_limite_del_admin_da_413_con_el_numero(admin_client):
    """Con un límite puesto, quien rechaza es el middleware, en JSON y con el
    número dentro — no la página HTML de 413 de nginx."""
    admin_client.put("/api/settings/platform", json={"max_request_bytes": 4096})
    r = admin_client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("grande.png", io.BytesIO(b"x" * 8192), "image/png")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "payload_too_large"
    assert r.json()["detail"]["limit_bytes"] == 4096


def test_avatar_campo_no_fichero_devuelve_400(client):
    """Reproduce el bug original: 'avatar' llega como texto, no como fichero."""
    _register_and_login(client, "avatarnotfile")
    r = client.post("/api/auth/me/avatar", data={"avatar": "no-soy-un-fichero"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "avatar_field_required"


def test_perfil_publico_avatar_url_presente_tras_subida(client):
    _register_and_login(client, "avprofile")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("av.png", io.BytesIO(png_bytes), "image/png")},
    )
    r = client.get("/api/users/avprofile")
    assert r.status_code == 200
    assert r.json()["avatar_url"] is not None
