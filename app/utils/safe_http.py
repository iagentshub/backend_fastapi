"""Transporte HTTP con DNS fijado para URLs configurables por usuarios.

La validación y la conexión usan la misma IP, evitando DNS rebinding. HTTPS
conserva el hostname original para SNI y validación del certificado.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Collection
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

from app.config.security import assert_safe_url, resolve_safe_host

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _origin(url: str) -> str:
    """Devuelve el origen canónico (scheme + host + puerto efectivo)."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Solo se permiten URLs http/https con hostname")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Puerto inválido") from exc
    hostname = parts.hostname.encode("idna").decode("ascii").lower()
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parts.scheme.lower()}://{display_host}:{port}"


def normalize_origins(origins: Collection[str]) -> frozenset[str]:
    """Normaliza una allowlist para que nunca dependa de barras o puertos implícitos."""
    return frozenset(_origin(origin) for origin in origins)


def assert_url_allowed(
    url: str, *, allowed_internal_origins: Collection[str] = ()
) -> None:
    """Valida la política sin abrir red: público o un origen interno exacto."""
    allowed = normalize_origins(allowed_internal_origins)
    if _origin(url) in allowed:
        return
    assert_safe_url(url)


def _resolve_host(host: str, port: int, *, allow_internal: bool) -> str:
    """Resuelve todas las IP y devuelve una sola, validada para la política."""
    if not allow_internal:
        return resolve_safe_host(host, port)
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("No se pudo resolver el hostname de la URL") from exc
    if not addresses:
        raise ValueError("El hostname de la URL no tiene direcciones")
    # El origen ya fue autorizado exactamente. Aun así se fija una de sus IP
    # para que la conexión no haga una segunda resolución susceptible a rebinding.
    return str(addresses[0][4][0]).split("%", 1)[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self, hostname: str, connect_ip: str, port: int, timeout: float | None
    ):
        super().__init__(hostname, port=port, timeout=timeout)
        self._connect_ip = connect_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._connect_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, hostname: str, connect_ip: str, port: int, timeout: float | None
    ):
        super().__init__(
            hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._connect_ip = connect_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_ip, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


class SafeHTTPResponse:
    """Adaptador pequeño compatible con los usos de ``urlopen`` del backend."""

    def __init__(
        self,
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
        url: str,
    ) -> None:
        self._response = response
        self._connection = connection
        self.url = url
        self.status = response.status
        self.reason = response.reason
        self.headers = response.headers

    def read(self, amount: int | None = None) -> bytes:
        return self._response.read() if amount is None else self._response.read(amount)

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._response)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def info(self) -> Any:
        return self.headers

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self) -> "SafeHTTPResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def safe_urlopen(
    request: urllib.request.Request | str,
    *,
    timeout: float | None = 20,
    max_redirects: int = 5,
    raise_for_status: bool = True,
    allowed_internal_origins: Collection[str] = (),
) -> SafeHTTPResponse:
    """Abre una URL autorizada fijando la IP y revalidando cada redirección.

    Por defecto solo permite destinos públicos. ``allowed_internal_origins``
    abre excepciones exactas (scheme, host y puerto), pensadas para servicios
    locales como Ollama; nunca autoriza una red o un hostname en cualquier
    puerto.
    """
    if isinstance(request, str):
        current = urllib.request.Request(request)
    else:
        current = request
    visited: set[str] = set()

    for _ in range(max_redirects + 1):
        url = current.full_url
        if url in visited:
            raise urllib.error.URLError("Bucle de redirecciones")
        visited.add(url)
        allowed = normalize_origins(allowed_internal_origins)
        current_origin = _origin(url)
        allow_internal = current_origin in allowed
        if not allow_internal:
            assert_safe_url(url)
        parts = urlsplit(url)
        hostname = (parts.hostname or "").encode("idna").decode("ascii")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as exc:
            raise urllib.error.URLError("Puerto inválido") from exc
        connect_ip = _resolve_host(hostname, port, allow_internal=allow_internal)
        connection_cls = (
            _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
        )
        connection = connection_cls(hostname, connect_ip, port, timeout)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        default_port = 443 if parts.scheme == "https" else 80
        display_host = f"[{hostname}]" if ":" in hostname else hostname
        host_header = display_host if port == default_port else f"{display_host}:{port}"
        headers = dict(current.header_items())
        headers["Host"] = host_header
        try:
            connection.request(
                current.get_method(),
                path,
                body=current.data,
                headers=headers,
            )
            raw_response = connection.getresponse()
        except Exception:
            connection.close()
            raise
        response = SafeHTTPResponse(raw_response, connection, url)
        if raw_response.status in _REDIRECT_STATUSES:
            location = raw_response.headers.get("Location")
            response.read()
            response.close()
            if not location:
                raise urllib.error.URLError("Redirección sin cabecera Location")
            next_url = urljoin(url, location)
            method = current.get_method()
            data = current.data
            if raw_response.status == 303 or (
                raw_response.status in (301, 302) and method == "POST"
            ):
                method, data = "GET", None
            next_headers = {
                key: value
                for key, value in current.header_items()
                if key.lower() not in {"host", "content-length"}
            }
            current = urllib.request.Request(
                next_url, data=data, headers=next_headers, method=method
            )
            continue
        if raise_for_status and raw_response.status >= 400:
            raise urllib.error.HTTPError(
                url,
                raw_response.status,
                str(raw_response.reason),
                raw_response.headers,
                response,
            )
        return response
    raise urllib.error.URLError("Demasiadas redirecciones")
