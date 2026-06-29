"""Storage and text extraction for knowledge items (URLs + documents)."""
from __future__ import annotations

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

MAX_CONTENT = 500_000  # max characters stored per item


def fetch_url_text(url: str) -> str:
    """Download a URL and return its plain text (max 2 MB)."""
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
    """Extract text from a TXT, MD, or PDF file."""
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
        self._db_path = db_path

    async def list(
        self, owner_id: Optional[str], type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, owner_id, type, title, source, char_count, folder_id, created_at, updated_at "
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
            return [dict(r) for r in rows]

    async def get(self, item_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    "SELECT * FROM knowledge_items WHERE id = ? AND owner_id = ?",
                    (item_id, owner_id),
                )
            else:
                row = await conn.fetchone(
                    "SELECT * FROM knowledge_items WHERE id = ?",
                    (item_id,),
                )
            return dict(row) if row else None

    async def save(
        self,
        *,
        type: str,
        title: str,
        source: str,
        content: str,
        owner_id: str,
        folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        item_id = uuid.uuid4().hex[:16]
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO knowledge_items "
                "(id, owner_id, type, title, source, content, char_count, folder_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, owner_id, type, title, source, content, len(content), folder_id, now, now),
            )
            await conn.commit()
        return await self.get(item_id)  # type: ignore[return-value]

    async def move(self, item_id: str, folder_id: Optional[str], owner_id: Optional[str]) -> bool:
        async with open_db() as conn:
            if owner_id is not None:
                exists = await conn.fetchone(
                    "SELECT id FROM knowledge_items WHERE id = ? AND owner_id = ?",
                    (item_id, owner_id),
                )
            else:
                exists = await conn.fetchone(
                    "SELECT id FROM knowledge_items WHERE id = ?",
                    (item_id,),
                )
            if not exists:
                return False
            if owner_id is not None:
                await conn.execute(
                    "UPDATE knowledge_items SET folder_id = ? WHERE id = ? AND owner_id = ?",
                    (folder_id, item_id, owner_id),
                )
            else:
                await conn.execute(
                    "UPDATE knowledge_items SET folder_id = ? WHERE id = ?",
                    (folder_id, item_id),
                )
            await conn.commit()
            return True

    async def delete(self, item_id: str, owner_id: Optional[str]) -> bool:
        async with open_db() as conn:
            if owner_id is not None:
                exists = await conn.fetchone(
                    "SELECT id FROM knowledge_items WHERE id = ? AND owner_id = ?",
                    (item_id, owner_id),
                )
            else:
                exists = await conn.fetchone(
                    "SELECT id FROM knowledge_items WHERE id = ?",
                    (item_id,),
                )
            if not exists:
                return False
            if owner_id is not None:
                await conn.execute(
                    "DELETE FROM knowledge_items WHERE id = ? AND owner_id = ?",
                    (item_id, owner_id),
                )
            else:
                await conn.execute(
                    "DELETE FROM knowledge_items WHERE id = ?", (item_id,)
                )
            await conn.commit()
            return True


class FolderStorage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def list(self, owner_id: str, section: Optional[str] = None) -> List[Dict[str, Any]]:
        params: list = [owner_id]
        query = "SELECT id, owner_id, section, name, created_at FROM knowledge_folders WHERE owner_id = ?"
        if section:
            query += " AND section = ?"
            params.append(section)
        query += " ORDER BY name ASC"
        async with open_db() as conn:
            rows = await conn.fetchall(query, params)
            return [dict(r) for r in rows]

    async def get(self, folder_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    "SELECT * FROM knowledge_folders WHERE id = ? AND owner_id = ?",
                    (folder_id, owner_id),
                )
            else:
                row = await conn.fetchone(
                    "SELECT * FROM knowledge_folders WHERE id = ?", (folder_id,)
                )
            return dict(row) if row else None

    async def create(self, owner_id: str, section: str, name: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        folder_id = uuid.uuid4().hex[:16]
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO knowledge_folders (id, owner_id, section, name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (folder_id, owner_id, section, name.strip(), now),
            )
            await conn.commit()
        return await self.get(folder_id)  # type: ignore[return-value]

    async def rename(self, folder_id: str, owner_id: str, name: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            exists = await conn.fetchone(
                "SELECT id FROM knowledge_folders WHERE id = ? AND owner_id = ?",
                (folder_id, owner_id),
            )
            if not exists:
                return None
            await conn.execute(
                "UPDATE knowledge_folders SET name = ? WHERE id = ? AND owner_id = ?",
                (name.strip(), folder_id, owner_id),
            )
            await conn.commit()
        return await self.get(folder_id)

    async def delete(self, folder_id: str, owner_id: str, cascade: bool = False) -> bool:
        async with open_db() as conn:
            async with conn.transaction():
                exists = await conn.fetchone(
                    "SELECT id FROM knowledge_folders WHERE id = ? AND owner_id = ?",
                    (folder_id, owner_id),
                )
                if not exists:
                    return False
                if cascade:
                    await conn.execute(
                        "DELETE FROM knowledge_items WHERE folder_id = ? AND owner_id = ?",
                        (folder_id, owner_id),
                    )
                else:
                    await conn.execute(
                        "UPDATE knowledge_items SET folder_id = NULL WHERE folder_id = ? AND owner_id = ?",
                        (folder_id, owner_id),
                    )
                await conn.execute(
                    "DELETE FROM knowledge_folders WHERE id = ? AND owner_id = ?",
                    (folder_id, owner_id),
                )
        return True
