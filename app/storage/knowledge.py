"""Storage and text extraction for knowledge items (URLs + documents)."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from app.storage.db import AsyncConn, open_db
from app.storage.resource_base import ResourceStorage
from app.storage.skill_storage import ensure_origin_label
from app.utils.generators import generate_date, generate_id
from app.utils.safe_http import safe_urlopen


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
    try:
        raw_labels = d.get("labels")
        d["labels"] = (
            json.loads(raw_labels) if isinstance(raw_labels, str) else raw_labels
        ) or ["private"]
        d["labels"] = ensure_origin_label([str(label) for label in d["labels"]])
    except (json.JSONDecodeError, TypeError):
        d["labels"] = ensure_origin_label(["private"])
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


def _download_safe_url(url: str) -> tuple[bytes, str]:
    """Descarga con DNS fijado y valida de nuevo cada redirección."""
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "iAgentsHub/1.0 (+knowledge-fetch)",
            "Accept-Encoding": "identity",
        },
    )
    with safe_urlopen(request, timeout=20, raise_for_status=False) as response:
        content_type = response.headers.get("Content-Type", "text/html")
        return response.read(_MAX_DOWNLOAD_BYTES), content_type


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

    async def list(
        self, owner_id: Optional[str], type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, owner_id, type, title, source, char_count, "
            "labels, is_active, deactivated_at, created_at, updated_at "
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
                f"labels, is_active, deactivated_at, created_at, updated_at "
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
        labels: Optional[List[str]] = None,
        item_id: Optional[str] = None,
        conn: Optional[AsyncConn] = None,
        assume_new: bool = False,
    ) -> Dict[str, Any]:
        now = generate_date()
        normalized_labels = ensure_origin_label(labels or ["private"])
        item_id = item_id or generate_id(16)
        existing = None if assume_new else await self.get(item_id, owner_id)
        target = conn
        if target is None:
            async with open_db() as own_conn:
                await self._save_text_row(
                    own_conn,
                    item_id,
                    owner_id,
                    type,
                    title,
                    source,
                    content,
                    normalized_labels,
                    existing["created_at"] if existing else now,
                    now,
                )
                await own_conn.commit()
            await self.sync_labels(item_id, owner_id, normalized_labels)
        else:
            await self._save_text_row(
                target,
                item_id,
                owner_id,
                type,
                title,
                source,
                content,
                normalized_labels,
                existing["created_at"] if existing else now,
                now,
            )
            await self.sync_labels(item_id, owner_id, normalized_labels, conn=target)
        return (
            await self.get(item_id, owner_id)
            if conn is None
            else {
                "id": item_id,
                "owner_id": owner_id,
                "type": type,
                "title": title,
                "name": title,
                "source": source,
                "content": content,
                "labels": normalized_labels,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            }
        )

    async def _save_text_row(
        self,
        conn: AsyncConn,
        item_id: str,
        owner_id: str,
        type: str,
        title: str,
        source: str,
        content: str,
        labels: List[str],
        created_at: str,
        updated_at: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO knowledge_items "
            "(id, owner_id, type, title, source, content, char_count, labels, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type,title=excluded.title,"
            "source=excluded.source,content=excluded.content,"
            "char_count=excluded.char_count,labels=excluded.labels,"
            "updated_at=excluded.updated_at",
            (
                item_id,
                owner_id,
                type,
                title,
                source,
                content,
                len(content),
                json.dumps(labels, ensure_ascii=False),
                created_at,
                updated_at,
            ),
        )

    async def delete(self, item_id: str, owner_id: Optional[str]) -> bool:
        cond, params = _owner_filter(item_id, owner_id)
        async with open_db() as conn:
            if not await conn.fetchone(
                f"SELECT id FROM knowledge_items WHERE {cond}", params
            ):
                return False
            await conn.execute(f"DELETE FROM knowledge_items WHERE {cond}", params)
            await conn.commit()
        await self.clear_labels(item_id)
        return True
