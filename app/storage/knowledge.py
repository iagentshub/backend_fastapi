"""Storage y extracción de texto para items de conocimiento (URLs + documentos)."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.storage.db import open_db


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

MAX_CONTENT = 500_000  # caracteres máximos almacenados por item


def fetch_url_text(url: str) -> str:
    """Descarga una URL y devuelve su texto plano (máx 2 MB)."""
    import urllib.request

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Solo se permiten URLs http/https")

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
    """Extrae texto de un fichero TXT, MD o PDF."""
    name_lower = (filename or "").lower()
    is_pdf = name_lower.endswith(".pdf") or "pdf" in mime.lower()

    if is_pdf:
        import io
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("pypdf no instalado — reconstruye la imagen Docker") from exc
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

class KnowledgeStorage:
    def __init__(self, db_path: Path) -> None:
        self._db = open_db(db_path)
        self._lock = threading.Lock()

    def list(
        self, owner_id: Optional[str], type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, owner_id, type, title, source, char_count, created_at, updated_at "
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
        return [dict(r) for r in self._db.execute(query, params).fetchall()]

    def get(self, item_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if owner_id is not None:
            row = self._db.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND owner_id = ?",
                (item_id, owner_id),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT * FROM knowledge_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        return dict(row) if row else None

    def save(
        self,
        *,
        type: str,
        title: str,
        source: str,
        content: str,
        owner_id: str,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        item_id = uuid.uuid4().hex[:16]
        with self._lock:
            self._db.execute(
                "INSERT INTO knowledge_items "
                "(id, owner_id, type, title, source, content, char_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, owner_id, type, title, source, content, len(content), now, now),
            )
            self._db.commit()
        return self.get(item_id)  # type: ignore[return-value]

    def delete(self, item_id: str, owner_id: Optional[str]) -> bool:
        if owner_id is not None:
            cur = self._db.execute(
                "DELETE FROM knowledge_items WHERE id = ? AND owner_id = ?",
                (item_id, owner_id),
            )
        else:
            cur = self._db.execute(
                "DELETE FROM knowledge_items WHERE id = ?", (item_id,)
            )
        self._db.commit()
        return cur.rowcount > 0
