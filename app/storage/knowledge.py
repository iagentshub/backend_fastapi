"""Storage and text extraction for knowledge items (URLs + documents)."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.security import assert_safe_url
from app.storage.db import open_db
from app.storage.resource_base import ResourceStorage
from app.utils.generators import generate_date, generate_id


def _owner_filter(item_id: str, owner_id: Optional[str]) -> tuple[str, tuple]:
    """Devuelve (fragmento WHERE, params) restringiendo por owner si se proporciona."""
    if owner_id is not None:
        return "id = ? AND owner_id = ?", (item_id, owner_id)
    return "id = ?", (item_id,)


def _coerce_active(d: Dict[str, Any]) -> Dict[str, Any]:
    """is_active llega como int 1/0; exponerlo como bool en la API."""
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


def fetch_url_text(url: str) -> str:
    """Download a URL and return its plain text (max 2 MB)."""
    import urllib.request

    # Validar que la URL no apunte a redes privadas o endpoints de metadata (SSRF)
    assert_safe_url(url)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "iAgentsHub/1.0 (+knowledge-fetch)"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        content_type: str = resp.headers.get("Content-Type", "text/html")
        raw = resp.read(2 * 1024 * 1024)

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
