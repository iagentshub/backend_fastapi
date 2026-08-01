"""Storage and text extraction for knowledge items (URLs + documents)."""

from __future__ import annotations

import http.client
import socket
import ssl
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlsplit

from app.config.security import assert_safe_url, resolve_safe_host
from app.storage.db import open_db
from app.storage.resource_base import ResourceStorage
from app.utils.generators import generate_date, generate_id


def _owner_filter(item_id: str, owner_id: Optional[str]) -> tuple[str, tuple]:
    """Devuelve (fragmento WHERE, params) restringiendo por owner si se proporciona."""
    if owner_id is not None:
        return "id = ? AND owner_id = ?", (item_id, owner_id)
    return "id = ?", (item_id,)


def _coerce_active(d: Dict[str, Any]) -> Dict[str, Any]:
    """Expose the common resource contract while keeping ``title`` compatible."""
    d["name"] = str(d.get("title") or d.get("name") or "")
    d["resource_type"] = "knowledge"
    d.setdefault("description", "")
    d.setdefault("icon", "")
    d.setdefault("scope", "private")
    d.setdefault("labels", ["private"])
    if "is_active" in d:
        d["is_active"] = bool(d["is_active"])
    return d


# ── HTML text extractor ────────────────────────────────────────────────────────


class _TextParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def text(self) -> str:
        return " ".join(self._parts)


# ── Public helpers ─────────────────────────────────────────────────────────────

MAX_CONTENT = 500_000  # max characters stored per item
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP conectado a una IP validada, conservando el Host original."""

    def __init__(self, hostname: str, connect_ip: str, port: int) -> None:
        super().__init__(hostname, port=port, timeout=20)
        self._connect_ip = connect_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._connect_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS fijado a una IP, con certificado y SNI del hostname original."""

    def __init__(self, hostname: str, connect_ip: str, port: int) -> None:
        super().__init__(
            hostname,
            port=port,
            timeout=20,
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


def _download_safe_url(url: str) -> tuple[bytes, str]:
    """Descarga con DNS fijado y valida de nuevo cada redirección."""
    current_url = url
    visited: set[str] = set()
    for _redirect_count in range(_MAX_REDIRECTS + 1):
        if current_url in visited:
            raise ValueError("Bucle de redirecciones al descargar la URL")
        visited.add(current_url)
        assert_safe_url(current_url)
        parts = urlsplit(current_url)
        hostname = (parts.hostname or "").encode("idna").decode("ascii")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as exc:
            raise ValueError("Puerto inválido en la URL") from exc
        connect_ip = resolve_safe_host(hostname, port)
        connection_class = (
            _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
        )
        connection = connection_class(hostname, connect_ip, port)
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parts.query:
            path += "?" + quote(parts.query, safe="=&?/:;+,%@!$'()*-._~")
        default_port = 443 if parts.scheme == "https" else 80
        display_host = f"[{hostname}]" if ":" in hostname else hostname
        host_header = display_host if port == default_port else f"{display_host}:{port}"
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host_header,
                    "User-Agent": "iAgentsHub/1.0 (+knowledge-fetch)",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in _REDIRECT_STATUSES:
                location = response.headers.get("Location")
                response.read(1)
                if not location:
                    raise ValueError("Redirección sin cabecera Location")
                current_url = urljoin(current_url, location)
                continue
            content_type = response.headers.get("Content-Type", "text/html")
            return response.read(_MAX_DOWNLOAD_BYTES), content_type
        finally:
            connection.close()
    raise ValueError("Demasiadas redirecciones al descargar la URL")


def fetch_url_text(url: str) -> str:
    """Download a URL and return its plain text (max 2 MB)."""
    raw, content_type = _download_safe_url(url)

    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].strip().split(";")[0].split(" ")[0]

    if "text/html" in content_type:
        parser = _TextParser()
        try:
            parser.feed(raw.decode(charset, errors="replace"))
        except Exception:
            parser.feed(raw.decode("utf-8", errors="replace"))
        return parser.text()[:MAX_CONTENT]

    return raw.decode(charset, errors="replace")[:MAX_CONTENT]


def extract_document_text(content_bytes: bytes, filename: str, mime: str = "") -> str:
    """Extract text from a TXT, MD, or PDF file."""
    name_lower = (filename or "").lower()
    is_pdf = name_lower.endswith(".pdf") or "pdf" in mime.lower()

    if is_pdf:
        import io

        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError(
                "pypdf no instalado — reconstruye la imagen Docker"
            ) from exc
        reader = PdfReader(io.BytesIO(content_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)[:MAX_CONTENT]

    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return content_bytes.decode(enc)[:MAX_CONTENT]
        except (UnicodeDecodeError, LookupError):
            continue
    return content_bytes.decode("utf-8", errors="replace")[:MAX_CONTENT]


# ── Storage ────────────────────────────────────────────────────────────────────


class KnowledgeStorage(ResourceStorage):
    table = "knowledge_items"
    resource_type = "knowledge"

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self._db_path = db_path

    async def list(
        self, owner_id: Optional[str], type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, owner_id, type, title, source, char_count, "
            "is_active, deactivated_at, created_at, updated_at "
            "FROM knowledge_items"
        )
        params: list = []
        where: list = []
        if owner_id is not None:
            where.append("owner_id = ?")
            params.append(owner_id)
        if type:
            where.append("type = ?")
            params.append(type)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC"
        async with open_db() as conn:
            rows = await conn.fetchall(query, params)
            return [_coerce_active(dict(r)) for r in rows]

    async def get(
        self, item_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        cond, params = _owner_filter(item_id, owner_id)
        async with open_db() as conn:
            row = await conn.fetchone(
                f"SELECT id, owner_id, type, title, source, content, char_count, "
                f"is_active, deactivated_at, created_at, updated_at "
                f"FROM knowledge_items WHERE {cond}",
                params,
            )
            return _coerce_active(dict(row)) if row else None

    async def save(
        self,
        *,
        type: str,
        title: str,
        source: str,
        content: str,
        owner_id: str,
    ) -> Dict[str, Any]:
        now = generate_date()
        item_id = generate_id(16)
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO knowledge_items "
                "(id, owner_id, type, title, source, content, char_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    owner_id,
                    type,
                    title,
                    source,
                    content,
                    len(content),
                    now,
                    now,
                ),
            )
            await conn.commit()
        return await self.get(item_id)  # type: ignore[return-value]

    async def delete(self, item_id: str, owner_id: Optional[str]) -> bool:
        cond, params = _owner_filter(item_id, owner_id)
        async with open_db() as conn:
            if not await conn.fetchone(
                f"SELECT id FROM knowledge_items WHERE {cond}", params
            ):
                return False
            await conn.execute(f"DELETE FROM knowledge_items WHERE {cond}", params)
            await conn.commit()
            return True
