"""Tests fase 1 — perfil social: PUT /me/profile, POST /me/avatar, GET /users/{username}."""
from __future__ import annotations

import io


def _register_and_login(client, username="socialuser", password="pass1234"):
    import asyncio

    from app.auth.auth import create_token, register_user
    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def test_actualizar_perfil_campos_basicos(client):
    _register_and_login(client)
    r = client.put("/api/auth/me/profile", json={
        "bio": "Desarrollador de agentes IA",
        "languages": ["es", "en"],
        "is_email_public": True,
        "github": "https://github.com/myghuser",
        "cv": "# Mi CV\n\nExperiencia en Python.",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    profile = client.get("/api/users/socialuser").json()
    assert profile["email_public"] == "socialuser@example.com"


def test_perfil_publico_devuelve_campos(client):
    _register_and_login(client, "perfiluser")
    client.put("/api/auth/me/profile", json={
        "bio": "Bio pública",
        "languages": ["es"],
        "github": "https://github.com/perfilgh",
    })
    r = client.get("/api/users/perfiluser")
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "perfiluser"
    assert data["bio"] == "Bio pública"
    assert "es" in data["languages"]
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
    client.put("/api/auth/me/profile", json={"languages": ["es", "xx", "klingon", "en"]})
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
    data = b"x" * (9 * 1024 * 1024)  # 9 MB, bajo el límite de 10 MB
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("big.jpg", io.BytesIO(data), "image/jpeg")},
    )
    assert r.status_code == 200


def test_avatar_mayor_10mb_rechazado_400_no_413(client):
    _register_and_login(client, "avatartoobig")
    data = b"x" * (10 * 1024 * 1024 + 1024)  # >10 MB, bajo el override de 11 MB del middleware
    r = client.post(
        "/api/auth/me/avatar",
        files={"avatar": ("toobig.jpg", io.BytesIO(data), "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "avatar_too_large"


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
