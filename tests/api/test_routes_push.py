"""Web Push: suscripción, reparto por canales y limpieza de destinos muertos.

Lo que aquí se fija no es que el cifrado sea correcto —de eso responden
`http_ece` y `py_vapid`— sino las decisiones de este producto: quién recibe,
quién deja de recibir y qué pasa cuando el servicio push dice que ya no existe.
"""
from __future__ import annotations

import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

_VAPID_DE_PRUEBA: tuple[str, str] | None = None


def _vapid_de_prueba() -> tuple[str, str]:
    """Par VAPID real generado al vuelo; firmar con uno inventado no vale."""
    global _VAPID_DE_PRUEBA
    if _VAPID_DE_PRUEBA is None:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01

        v = Vapid01()
        v.generate_keys()
        privada = (
            base64.urlsafe_b64encode(
                v.private_key.private_numbers().private_value.to_bytes(32, "big")
            )
            .rstrip(b"=")
            .decode()
        )
        publica = (
            base64.urlsafe_b64encode(
                v.public_key.public_bytes(
                    serialization.Encoding.X962,
                    serialization.PublicFormat.UncompressedPoint,
                )
            )
            .rstrip(b"=")
            .decode()
        )
        _VAPID_DE_PRUEBA = (publica, privada)
    return _VAPID_DE_PRUEBA


def _claves_navegador() -> tuple[str, str]:
    """Las que entrega `PushManager.subscribe()`: clave pública y secreto."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    k = ec.generate_private_key(ec.SECP256R1())
    p256dh = (
        base64.urlsafe_b64encode(
            k.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )
        .rstrip(b"=")
        .decode()
    )
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return p256dh, auth


@pytest.fixture()
def push_activo(monkeypatch):
    """Instalación con VAPID configurado."""
    from app.config import session as cfg

    publica, privada = _vapid_de_prueba()
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", publica)
    monkeypatch.setattr(cfg, "VAPID_PRIVATE_KEY", privada)
    monkeypatch.setattr(cfg, "VAPID_SUBJECT", "mailto:hub@test.local")
    return publica


def _auth(client: TestClient, username: str) -> TestClient:
    import asyncio

    from app.auth.auth import create_token, register_user

    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token(username))
    return client


async def _auth_async(client: TestClient, username: str) -> TestClient:
    """`_auth` para tests async: `asyncio.run` no vale dentro de un bucle vivo."""
    from app.auth.auth import create_token, register_user

    try:
        await register_user(username, "pass1234", email=f"{username}@test.com")
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token(username))
    return client


def _suscribir(client: TestClient, endpoint: str) -> None:
    p256dh, auth = _claves_navegador()
    r = client.post(
        "/api/notifications/push/subscribe",
        json={"endpoint": endpoint, "p256dh": p256dh, "auth": auth},
    )
    assert r.status_code == 200, r.text


class _RespuestaFalsa:
    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


def _cliente_secuencia(codigos: list[int], intentos: list, cabeceras: dict | None = None):
    """Devuelve un código distinto en cada intento y cuenta las llamadas."""

    class ClienteFalso:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, content=None, headers=None):
            codigo = codigos[min(len(intentos), len(codigos) - 1)]
            intentos.append(codigo)
            return _RespuestaFalsa(codigo, cabeceras or {})

    return lambda **_: ClienteFalso()


def _cliente_falso(status_code: int, capturado: dict | None = None):
    """Sustituto de httpx.AsyncClient que no sale a la red."""

    class ClienteFalso:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, content=None, headers=None):
            if capturado is not None:
                capturado["url"] = url
                capturado["cuerpo"] = content
                capturado["cabeceras"] = headers
            return _RespuestaFalsa(status_code)

    return lambda **_: ClienteFalso()


# ── La clave pública ──────────────────────────────────────────────────────────

def test_sin_vapid_el_cliente_sabe_que_no_hay_push(client):
    """Sin clave no hay nada que activar, y el cliente no debe ofrecerlo."""
    _auth(client, "push_sinvapid")
    r = client.get("/api/notifications/push/key")
    assert r.status_code == 200
    assert r.json() == {"key": None, "enabled": False}


def test_con_vapid_se_entrega_la_clave(client, push_activo):
    _auth(client, "push_convapid")
    datos = client.get("/api/notifications/push/key").json()
    assert datos["enabled"] is True
    assert datos["key"] == push_activo


def test_la_clave_exige_sesion(client):
    assert client.get("/api/notifications/push/key").status_code == 401


def test_sin_contacto_vapid_no_hay_push(client, monkeypatch):
    """`sub` parece opcional y no lo es: sin él `py_vapid.sign()` lanza.

    Trataba el contacto como adorno y con dos claves de tres el interruptor se
    ofrecía, el usuario lo activaba, y ningún envío salía. Mejor apagado.
    """
    from app.config import session as cfg

    publica, privada = _vapid_de_prueba()
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", publica)
    monkeypatch.setattr(cfg, "VAPID_PRIVATE_KEY", privada)
    monkeypatch.setattr(cfg, "VAPID_SUBJECT", "")

    _auth(client, "push_sinsub")
    assert client.get("/api/notifications/push/key").json()["enabled"] is False


# ── Alta y baja ───────────────────────────────────────────────────────────────

def test_suscribirse_y_darse_de_baja(client, push_activo):
    _auth(client, "push_alta")
    p256dh, auth = _claves_navegador()
    endpoint = "https://push.example.com/abc"

    r = client.post(
        "/api/notifications/push/subscribe",
        json={"endpoint": endpoint, "p256dh": p256dh, "auth": auth},
    )
    assert r.status_code == 200
    assert r.json()["devices"] == 1

    r = client.request(
        "DELETE", "/api/notifications/push/subscribe", json={"endpoint": endpoint}
    )
    assert r.status_code == 200
    assert r.json()["devices"] == 0


def test_resuscribirse_no_duplica(client, push_activo):
    """El navegador reenvía el mismo endpoint en cada arranque de la app."""
    _auth(client, "push_dup")
    for _ in range(3):
        _suscribir(client, "https://push.example.com/mismo")

    import asyncio

    from app.auth.auth import get_user_by_username
    from app.storage import push_subscriptions as subs

    user = asyncio.run(get_user_by_username("push_dup"))
    assert asyncio.run(subs.count_for_user(user["id"])) == 1


def test_se_rechaza_un_endpoint_que_no_es_https(client, push_activo):
    _auth(client, "push_http")
    p256dh, auth = _claves_navegador()
    r = client.post(
        "/api/notifications/push/subscribe",
        json={"endpoint": "http://push.example.com/x", "p256dh": p256dh, "auth": auth},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_field"


def test_sin_vapid_no_se_puede_suscribir(client):
    _auth(client, "push_nosub")
    p256dh, auth = _claves_navegador()
    r = client.post(
        "/api/notifications/push/subscribe",
        json={"endpoint": "https://push.example.com/y", "p256dh": p256dh, "auth": auth},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "push_unavailable"


def test_el_invitado_no_se_suscribe(client, push_activo):
    client.post("/api/auth/guest")
    p256dh, auth = _claves_navegador()
    r = client.post(
        "/api/notifications/push/subscribe",
        json={"endpoint": "https://push.example.com/z", "p256dh": p256dh, "auth": auth},
    )
    assert r.status_code == 403


# ── El envío ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_el_envio_cifra_y_firma(client, push_activo, monkeypatch):
    """Fija las cabeceras del protocolo, que es lo que el servicio rechaza."""
    await _auth_async(client, "push_envio")
    _suscribir(client, "https://push.example.com/envio")

    capturado: dict = {}
    import app.services.push as push_mod

    monkeypatch.setattr(
        push_mod.httpx, "AsyncClient", _cliente_falso(201, capturado)
    )

    from app.auth.auth import get_user_by_username

    user = await get_user_by_username("push_envio")
    enviados = await push_mod.send_push(
        user_id=user["id"], kind="group_invite", data={"actor": "ana", "group": "M"}
    )

    assert enviados == 1
    assert capturado["url"] == "https://push.example.com/envio"
    assert capturado["cabeceras"]["Content-Encoding"] == "aes128gcm"
    assert capturado["cabeceras"]["Authorization"].startswith("WebPush ")
    assert capturado["cabeceras"]["TTL"]
    # El payload viaja cifrado: ni el tipo ni los datos pueden verse en claro.
    assert b"group_invite" not in capturado["cuerpo"]
    assert b"ana" not in capturado["cuerpo"]


@pytest.mark.asyncio
async def test_un_410_borra_la_suscripcion(client, push_activo, monkeypatch):
    """Es el mecanismo estándar de limpieza, no un error que reintentar."""
    await _auth_async(client, "push_muerta")
    _suscribir(client, "https://push.example.com/muerta")

    import app.services.push as push_mod

    monkeypatch.setattr(push_mod.httpx, "AsyncClient", _cliente_falso(410))

    from app.auth.auth import get_user_by_username
    from app.storage import push_subscriptions as subs

    user = await get_user_by_username("push_muerta")
    assert await push_mod.send_push(user_id=user["id"], kind="x", data={}) == 0
    assert await subs.count_for_user(user["id"]) == 0


@pytest.mark.asyncio
async def test_sin_vapid_no_se_intenta_enviar():
    from app.services import push as push_mod

    assert await push_mod.send_push(user_id="quien-sea", kind="x", data={}) == 0


@pytest.mark.asyncio
async def test_notify_no_espera_al_servicio_push(client, push_activo, monkeypatch):
    """El aviso se encola; la petición que lo provocó no paga su latencia.

    Invitar a alguien no puede tardar lo que tarde FCM. El correo ya salía en un
    pool de hilos y el push se recorría con `await` dentro del handler: con tres
    navegadores suscritos y un servicio lento eran decenas de segundos.
    """
    import asyncio

    await _auth_async(client, "push_async")
    _suscribir(client, "https://push.example.com/async")

    llamadas: list[str] = []
    empezado = asyncio.Event()
    soltar = asyncio.Event()

    class ClienteLento:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, content=None, headers=None):
            llamadas.append(url)
            empezado.set()
            await soltar.wait()   # se queda colgado a propósito
            return _RespuestaFalsa(201)

    import app.services.push as push_mod

    monkeypatch.setattr(push_mod.httpx, "AsyncClient", lambda **_: ClienteLento())

    from app.auth.auth import get_user_by_username
    from app.services.notifications import notify
    from app.storage.notifications import count_unread

    user = await get_user_by_username("push_async")

    # Con el servicio push colgado, `notify` tiene que volver igualmente.
    await asyncio.wait_for(
        notify(user_id=user["id"], kind="group_invite", actor="ana", group="M"),
        timeout=2.0,
    )
    assert await count_unread(user["id"]) == 1, "la campana se guarda siempre"

    # Y el envío está de verdad en marcha, no descartado.
    await asyncio.wait_for(empezado.wait(), timeout=2.0)
    assert llamadas == ["https://push.example.com/async"]
    soltar.set()
    await asyncio.sleep(0)


# ── Reintentos ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_un_503_se_reintenta_y_acaba_entregando(client, push_activo, monkeypatch):
    """Un pico del servicio push no debe costar el aviso."""
    await _auth_async(client, "push_reintento")
    _suscribir(client, "https://push.example.com/reintento")

    intentos: list[int] = []
    import app.services.push as push_mod

    monkeypatch.setattr(push_mod, "_ESPERA_BASE", 0.001)
    monkeypatch.setattr(
        push_mod.httpx, "AsyncClient", _cliente_secuencia([503, 201], intentos)
    )

    from app.auth.auth import get_user_by_username

    user = await get_user_by_username("push_reintento")
    assert await push_mod.send_push(user_id=user["id"], kind="x", data={}) == 1
    assert intentos == [503, 201]


@pytest.mark.asyncio
async def test_un_400_no_se_reintenta(client, push_activo, monkeypatch):
    """El mensaje está mal: repetirlo da el mismo error y gasta cuota."""
    await _auth_async(client, "push_no_reintento")
    _suscribir(client, "https://push.example.com/no_reintento")

    intentos: list[int] = []
    import app.services.push as push_mod

    monkeypatch.setattr(push_mod, "_ESPERA_BASE", 0.001)
    monkeypatch.setattr(
        push_mod.httpx, "AsyncClient", _cliente_secuencia([400], intentos)
    )

    from app.auth.auth import get_user_by_username

    user = await get_user_by_username("push_no_reintento")
    assert await push_mod.send_push(user_id=user["id"], kind="x", data={}) == 0
    assert intentos == [400], "un 400 no debe reintentarse"


@pytest.mark.asyncio
async def test_un_410_no_se_reintenta_y_limpia(client, push_activo, monkeypatch):
    """La suscripción ya no existe: reintentar solo repetiría el 410."""
    await _auth_async(client, "push_410_unico")
    _suscribir(client, "https://push.example.com/410unico")

    intentos: list[int] = []
    import app.services.push as push_mod

    monkeypatch.setattr(push_mod, "_ESPERA_BASE", 0.001)
    monkeypatch.setattr(
        push_mod.httpx, "AsyncClient", _cliente_secuencia([410], intentos)
    )

    from app.auth.auth import get_user_by_username
    from app.storage import push_subscriptions as subs

    user = await get_user_by_username("push_410_unico")
    assert await push_mod.send_push(user_id=user["id"], kind="x", data={}) == 0
    assert intentos == [410]
    assert await subs.count_for_user(user["id"]) == 0


@pytest.mark.asyncio
async def test_se_rinde_tras_los_intentos_pactados(client, push_activo, monkeypatch):
    """Un servicio caído no puede dejar la tarea colgada indefinidamente."""
    await _auth_async(client, "push_rendido")
    _suscribir(client, "https://push.example.com/rendido")

    intentos: list[int] = []
    import app.services.push as push_mod

    monkeypatch.setattr(push_mod, "_ESPERA_BASE", 0.001)
    monkeypatch.setattr(
        push_mod.httpx, "AsyncClient", _cliente_secuencia([503], intentos)
    )

    from app.auth.auth import get_user_by_username

    user = await get_user_by_username("push_rendido")
    assert await push_mod.send_push(user_id=user["id"], kind="x", data={}) == 0
    assert len(intentos) == push_mod._INTENTOS


@pytest.mark.asyncio
async def test_se_respeta_retry_after(client, push_activo, monkeypatch):
    """Adelantarse al plazo que pide el servicio es como un 429 se vuelve bloqueo."""
    await _auth_async(client, "push_retryafter")
    _suscribir(client, "https://push.example.com/retryafter")

    intentos: list[int] = []
    esperas: list[float] = []
    import app.services.push as push_mod

    async def _dormir(segundos):
        esperas.append(segundos)

    monkeypatch.setattr(push_mod.asyncio, "sleep", _dormir)
    monkeypatch.setattr(push_mod, "_ESPERA_BASE", 0.001)
    monkeypatch.setattr(
        push_mod.httpx,
        "AsyncClient",
        _cliente_secuencia([429, 201], intentos, {"retry-after": "5"}),
    )

    from app.auth.auth import get_user_by_username

    user = await get_user_by_username("push_retryafter")
    assert await push_mod.send_push(user_id=user["id"], kind="x", data={}) == 1
    assert esperas == [5.0], "debe esperar lo que pidió el servicio, no su base"


@pytest.mark.asyncio
async def test_un_retry_after_absurdo_se_recorta(client, push_activo, monkeypatch):
    """Media hora dormido no vale un aviso que ya está en la campana."""
    await _auth_async(client, "push_retryabsurdo")
    _suscribir(client, "https://push.example.com/retryabsurdo")

    esperas: list[float] = []
    import app.services.push as push_mod

    async def _dormir(segundos):
        esperas.append(segundos)

    monkeypatch.setattr(push_mod.asyncio, "sleep", _dormir)
    monkeypatch.setattr(
        push_mod.httpx,
        "AsyncClient",
        _cliente_secuencia([429, 201], [], {"retry-after": "3600"}),
    )

    from app.auth.auth import get_user_by_username

    user = await get_user_by_username("push_retryabsurdo")
    await push_mod.send_push(user_id=user["id"], kind="x", data={})
    assert esperas == [push_mod._ESPERA_MAXIMA]


# ── Preferencias por canal ────────────────────────────────────────────────────

def test_los_canales_vienen_activados(client):
    _auth(client, "push_prefs_default")
    datos = client.get("/api/settings").json()
    assert datos["notify_email"] is True
    assert datos["notify_push"] is True


def test_apagar_un_canal_se_guarda(client):
    _auth(client, "push_prefs_off")
    r = client.put("/api/settings", json={"notify_email": False})
    assert r.status_code == 200
    assert r.json()["notify_email"] is False
    assert r.json()["notify_push"] is True
    assert client.get("/api/settings").json()["notify_email"] is False


@pytest.mark.asyncio
async def test_apagar_el_correo_no_apaga_la_campana(client, monkeypatch):
    """Los canales se apagan; el registro de lo que pasó, nunca."""
    import app.services.email as email_mod

    enviados: list[str] = []
    monkeypatch.setattr(email_mod, "_smtp_available", lambda: True)
    monkeypatch.setattr(email_mod, "_send_smtp", lambda to, s, h: enviados.append(to))

    await _auth_async(client, "push_prefs_bell")
    client.put("/api/settings", json={"notify_email": False})

    from app.auth.auth import get_user_by_username
    from app.services.notifications import notify
    from app.storage.notifications import count_unread

    user = await get_user_by_username("push_prefs_bell")
    await notify(user_id=user["id"], kind="group_invite", actor="ana", group="M")

    assert enviados == []
    assert await count_unread(user["id"]) == 1


def test_las_preferencias_viajan_en_el_json(client):
    """Se guardan en `users.preferences`, que es un blob JSON."""
    _auth(client, "push_prefs_json")
    client.put("/api/settings", json={"notify_push": False})
    import asyncio

    from app.auth.auth import get_user_by_username

    user = asyncio.run(get_user_by_username("push_prefs_json"))
    assert json.loads(user["preferences"])["notify_push"] is False
