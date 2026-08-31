"""Storage and text extraction for knowledge items (URLs + documents)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from app.pagination.models import CursorPage, CursorParams
from app.services.resource_visibility import VisibilityFilter
from app.sql import sql
from app.storage.db import AsyncConn, open_db
from app.storage.knowledge_cursor_page import fetch_visible_knowledge_cursor_page
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
    if "content_truncated" in d:
        d["content_truncated"] = bool(d["content_truncated"])
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

# Cota de proceso, no de producto. Hasta 2026-08 esto valía 500 000 y era el
# recorte real de todo lo que se importaba: un PDF de 62 KB con 400 páginas
# perdía el 69 % de su texto, sin log, sin marca en la ficha y sin nada en la
# interfaz — el original no se guarda, así que lo cortado no se podía recuperar.
# El número de hoy está por encima de cualquier documento legítimo (unas 5 000
# páginas de libro) y lo único que defiende es la memoria del proceso. Cuando se
# alcanza ya no se pierde en silencio: se anota en la ficha y se registra.
# Ver docs/adr/013-la-extraccion-no-pierde-texto-en-silencio.md
MAX_EXTRACTED_CHARS = 20_000_000

# Lo mismo para la descarga de una URL: 2 MB cortaba páginas a mitad de HTML.
# Defensa del proceso, no política del admin: no es configurable a propósito.
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# Un PDF malformado —contenidos anidados, fuentes rotas, árbol de páginas con
# ciclos— puede tener a pypdf dando vueltas sin lanzar nada. El reloj se mira
# entre páginas, que es donde sí se puede abandonar; dentro de una página no hay
# forma de interrumpir a pypdf desde Python.
_PDF_DEADLINE_SECONDS = 120.0


@dataclass(frozen=True)
class ExtractedDocument:
    """Texto extraído y, si no cupo entero, por qué.

    Existe para que truncar deje de ser invisible. `extract_document_text`
    devuelve solo el texto y sigue valiendo donde los metadatos dan igual, pero
    ninguna ruta de `app/api/routes/` puede usarla: `tests/storage/
    test_extraccion_sin_perdida_silenciosa.py` falla si reaparece allí.
    """

    text: str
    truncated: bool = False
    source_chars: int = 0
    reason: str = ""

    @property
    def lost_chars(self) -> int:
        return max(0, self.source_chars - len(self.text))


def _bounded(text: str, *, source_chars: int | None = None) -> ExtractedDocument:
    """Aplica la cota de proceso dejando constancia si llega a morder."""
    total = len(text) if source_chars is None else source_chars
    if len(text) <= MAX_EXTRACTED_CHARS:
        return ExtractedDocument(text=text, source_chars=total)
    return ExtractedDocument(
        text=text[:MAX_EXTRACTED_CHARS],
        truncated=True,
        source_chars=total,
        reason="max_chars",
    )


def _download_safe_url(url: str) -> tuple[bytes, str, bool]:
    """Descarga con DNS fijado y valida de nuevo cada redirección.

    El tercer elemento dice si la respuesta llegó al tope y quedó cortada. Se
    lee un byte de más justo para poder distinguirlo: leer exactamente el tope
    devuelve lo mismo para una página que cabe justa y para una que no cabe.
    """
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
        raw = response.read(_MAX_DOWNLOAD_BYTES + 1)
        if len(raw) > _MAX_DOWNLOAD_BYTES:
            return raw[:_MAX_DOWNLOAD_BYTES], content_type, True
        return raw, content_type, False


def fetch_url_document(url: str) -> ExtractedDocument:
    """Descarga una URL y devuelve su texto plano, diciendo si no cupo entero."""
    raw, content_type, download_truncated = _download_safe_url(url)

    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].strip().split(";")[0].split(" ")[0]

    if "text/html" in content_type:
        parser = _TextParser()
        try:
            parser.feed(raw.decode(charset, errors="replace"))
        except LookupError:
            # El charset anunciado en el Content-Type no existe en Python
            # (`errors="replace"` ya descarta el UnicodeDecodeError). Se reintenta
            # en UTF-8, que es lo que sirve la web moderna. No se registra: pasa
            # con cabeceras mal escritas y el reintento resuelve el caso.
            parser.feed(raw.decode("utf-8", errors="replace"))
        extracted = _bounded(parser.text())
    else:
        extracted = _bounded(raw.decode(charset, errors="replace"))

    if download_truncated and not extracted.truncated:
        # El texto extraído cabe entero, pero viene de una descarga que se
        # cortó: sin esto la ficha diría que está completa.
        return ExtractedDocument(
            text=extracted.text,
            truncated=True,
            source_chars=len(extracted.text),
            reason="max_download_bytes",
        )
    return extracted


def fetch_url_text(url: str) -> str:
    """Texto plano de una URL, sin los metadatos de recorte."""
    return fetch_url_document(url).text


def _extract_pdf(content_bytes: bytes) -> ExtractedDocument:
    """Recorre las páginas acumulando, y para en cuanto sabe que sobra."""
    import io

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("pypdf no instalado — reconstruye la imagen Docker") from exc

    reader = PdfReader(io.BytesIO(content_bytes))
    total_pages = len(reader.pages)
    deadline = time.monotonic() + _PDF_DEADLINE_SECONDS

    pages: list[str] = []
    chars = 0
    reason = ""
    pages_read = 0
    for page in reader.pages:
        # El corte se decidía antes con un `[:MAX_CONTENT]` sobre el resultado:
        # se extraía el documento entero para tirar casi todo. Cortando aquí, un
        # documento largo legítimo deja de pagar el trabajo que ya se sabe
        # descartado.
        if chars >= MAX_EXTRACTED_CHARS:
            reason = "max_chars"
            break
        if time.monotonic() > deadline:
            reason = "timeout"
            break
        text = page.extract_text() or ""
        pages.append(text)
        chars += len(text) + 1
        pages_read += 1

    joined = "\n".join(pages)
    if not reason:
        return _bounded(joined)
    return ExtractedDocument(
        text=joined[:MAX_EXTRACTED_CHARS],
        truncated=True,
        # No se leyó el resto, así que el total real no se conoce: se estima con
        # lo que costó de media lo leído. Es una cifra para enseñar al usuario,
        # no un dato exacto, y quedarse con 0 sería peor.
        source_chars=(
            int(len(joined) * total_pages / pages_read) if pages_read else len(joined)
        ),
        reason=reason,
    )


def _extract_image(content_bytes: bytes) -> ExtractedDocument:
    import io

    try:
        import pytesseract
        from PIL import Image, UnidentifiedImageError
        from pillow_heif import register_heif_opener
    except ImportError as exc:
        raise ValueError("OCR no instalado — reconstruye la imagen Docker") from exc

    # Evita que una imagen comprimida pequena expanda a una cantidad de
    # memoria desproporcionada durante la decodificacion.
    Image.MAX_IMAGE_PIXELS = 40_000_000
    register_heif_opener()
    try:
        with Image.open(io.BytesIO(content_bytes)) as source:
            source.verify()
        with Image.open(io.BytesIO(content_bytes)) as source:
            source.load()
            image = source.convert("RGB")
            image.thumbnail((5000, 5000))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("La imagen no es valida o no se puede leer") from exc
    try:
        return _bounded(pytesseract.image_to_string(image, lang="spa+eng", timeout=30))
    except (RuntimeError, pytesseract.TesseractError) as exc:
        raise ValueError("No se pudo extraer texto de la imagen") from exc


def extract_document(
    content_bytes: bytes, filename: str, mime: str = ""
) -> ExtractedDocument:
    """Extrae el texto de un documento, PDF o imagen, y si sobró algo, lo dice."""
    name_lower = (filename or "").lower()
    is_pdf = name_lower.endswith(".pdf") or "pdf" in mime.lower()
    is_image = name_lower.endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")
    ) or mime.lower().startswith("image/")

    if is_pdf:
        return _extract_pdf(content_bytes)
    if is_image:
        return _extract_image(content_bytes)

    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return _bounded(content_bytes.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    return _bounded(content_bytes.decode("utf-8", errors="replace"))


def extract_document_text(content_bytes: bytes, filename: str, mime: str = "") -> str:
    """Texto de un documento, sin los metadatos de recorte.

    Ninguna ruta puede usar esta: perder el `truncated` es exactamente cómo un
    documento a medias acababa guardado como si estuviera entero. Lo guarda
    `tests/storage/test_extraccion_sin_perdida_silenciosa.py`.
    """
    return extract_document(content_bytes, filename, mime).text


class KnowledgeStorage(ResourceStorage):
    table = "knowledge_items"
    resource_type = "knowledge"

    async def list_visible_cursor_page(
        self,
        *,
        user: str,
        owner_id: str,
        type: str | None,
        page: CursorParams,
        permission_filter: VisibilityFilter | None = None,
        requested_group_id: str | None = None,
        catalog_filter: VisibilityFilter | None = None,
    ) -> CursorPage[Dict[str, Any]]:
        """Página keyset para consumidores cursor-only del catálogo."""

        return await fetch_visible_knowledge_cursor_page(
            user=user,
            owner_id=owner_id,
            type=type,
            page=page,
            permission_filter=permission_filter,
            requested_group_id=requested_group_id,
            catalog_filter=catalog_filter,
            decode=lambda row: _coerce_active(dict(row)),
            annotate=self._annotate_shared_page,
        )

    async def _annotate_shared_page(
        self,
        conn: AsyncConn,
        items: List[Dict[str, Any]],
        *,
        user: str,
        owner_id: str,
        requested_group_id: str | None,
    ) -> None:
        candidates = [
            item
            for item in items
            if requested_group_id is not None or item.get("owner_id") != owner_id
        ]
        if not candidates:
            return
        ids = [str(item["id"]) for item in candidates]
        placeholders = ",".join("?" for _ in ids)
        membership_sql: str
        suffix_params: tuple[Any, ...]
        if requested_group_id is not None:
            membership_sql = "AND s.group_id=?"
            suffix_params = (requested_group_id,)
        else:
            membership_sql = (
                "AND EXISTS (SELECT 1 FROM group_members m JOIN groups g "
                "ON g.id=m.group_id WHERE m.group_id=s.group_id "
                "AND m.username=? AND g.is_active=1)"
            )
            suffix_params = (user,)
        direct_rows = await conn.fetchall(
            "SELECT s.resource_id,MIN(s.group_id) FROM resource_group_shares s "
            "WHERE s.resource_type='knowledge' "
            f"AND s.resource_id IN ({placeholders}) {membership_sql} "
            "GROUP BY s.resource_id",
            (*ids, *suffix_params),
        )
        direct = {str(row[0]): str(row[1]) for row in direct_rows}
        packs = {
            str(item["pack_id"])
            for item in candidates
            if item.get("pack_id") is not None
        }
        pack_groups: dict[str, str] = {}
        if packs:
            pack_placeholders = ",".join("?" for _ in packs)
            pack_rows = await conn.fetchall(
                "SELECT s.resource_id,MIN(s.group_id) FROM resource_group_shares s "
                "WHERE s.resource_type='knowledge_pack' "
                f"AND s.resource_id IN ({pack_placeholders}) {membership_sql} "
                "GROUP BY s.resource_id",
                (*packs, *suffix_params),
            )
            pack_groups = {str(row[0]): str(row[1]) for row in pack_rows}
        for item in candidates:
            item_id = str(item["id"])
            if item_id in direct:
                item["_shared"] = True
                item["_group_id"] = direct[item_id]
                continue
            pack_group = pack_groups.get(str(item.get("pack_id") or ""))
            if pack_group is not None:
                item["_shared"] = True
                item["_group_id"] = pack_group
                item["_shared_via_pack"] = True

    async def list(
        self, owner_id: Optional[str], type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT k.id, k.owner_id, k.type, k.title, k.source, k.char_count, "
            "k.source_char_count, k.content_truncated, k.truncation_reason, "
            "k.mime_type, k.size_bytes, k.checksum, "
            "k.labels, k.is_active, k.deactivated_at, k.created_at, k.updated_at, "
            "k.pack_id, k.pack_relative_path, k.pack_kind FROM knowledge_items k"
        )
        params: list = []
        where: list = []
        if owner_id is not None:
            where.append("k.owner_id = ?")
            params.append(owner_id)
        if type:
            where.append("k.type = ?")
            params.append(type)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY k.created_at DESC"
        async with open_db() as conn:
            rows = await conn.fetchall(query, params)
            return [_coerce_active(dict(r)) for r in rows]

    async def pack_locations(self, item_ids: List[str]) -> Dict[str, Dict[str, str]]:
        """Pack al que pertenece cada ítem, en una sola consulta y sin contenido.

        El catálogo necesita dos columnas por fila para pintar la procedencia.
        Resolverlo con un ``get()`` por elemento leía la columna ``content``
        —el documento entero— de cada resultado de la página.
        """
        unique_ids = list(dict.fromkeys(i for i in item_ids if i))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" * len(unique_ids))
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT id, pack_id, pack_relative_path FROM knowledge_items "
                f"WHERE id IN ({placeholders}) AND pack_id IS NOT NULL",
                tuple(unique_ids),
            )
        return {
            str(row["id"]): {
                "pack_id": str(row["pack_id"]),
                "pack_relative_path": str(row["pack_relative_path"] or ""),
            }
            for row in rows
        }

    async def get(
        self, item_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if owner_id is not None:
            cond, params = "k.id = ? AND k.owner_id = ?", (item_id, owner_id)
        else:
            cond, params = "k.id = ?", (item_id,)
        async with open_db() as conn:
            row = await conn.fetchone(
                f"SELECT k.id, k.owner_id, k.type, k.title, k.source, k.content, k.char_count, "
                f"k.source_char_count, k.content_truncated, k.truncation_reason, "
                f"k.mime_type, k.size_bytes, k.checksum, "
                f"k.labels, k.is_active, k.deactivated_at, k.created_at, k.updated_at, "
                f"k.pack_id, k.pack_relative_path, k.pack_kind FROM knowledge_items k "
                f"WHERE {cond}",
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
        mime_type: str = "",
        size_bytes: int = 0,
        checksum: Optional[str] = None,
        extraction: Optional[ExtractedDocument] = None,
        item_id: Optional[str] = None,
        conn: Optional[AsyncConn] = None,
        assume_new: bool = False,
    ) -> Dict[str, Any]:
        now = generate_date()
        normalized_labels = ensure_origin_label(labels or ["private"])
        normalized_checksum = checksum or hashlib.sha256(content.encode()).hexdigest()
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
                    mime_type,
                    size_bytes,
                    normalized_checksum,
                    extraction,
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
                mime_type,
                size_bytes,
                normalized_checksum,
                extraction,
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
                "char_count": len(content),
                "source_char_count": (
                    extraction.source_chars if extraction else len(content)
                ),
                "content_truncated": bool(extraction and extraction.truncated),
                "truncation_reason": extraction.reason if extraction else "",
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "checksum": normalized_checksum,
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
        mime_type: str,
        size_bytes: int,
        checksum: str,
        extraction: Optional[ExtractedDocument],
        created_at: str,
        updated_at: str,
    ) -> None:
        # Sin `extraction` el contenido llega de una edición manual o de una
        # copia, no de una extracción: no hay nada que se haya quedado fuera.
        await conn.execute(
            sql("queries/knowledge:upsert_item"),
            (
                item_id,
                owner_id,
                type,
                title,
                source,
                content,
                len(content),
                extraction.source_chars if extraction else len(content),
                1 if extraction and extraction.truncated else 0,
                extraction.reason if extraction else "",
                mime_type,
                size_bytes,
                checksum,
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

    async def update_labels(
        self, item_id: str, owner_id: Optional[str], labels: List[str]
    ) -> bool:
        cond, params = _owner_filter(item_id, owner_id)
        async with open_db() as conn:
            row = await conn.fetchone(
                f"SELECT owner_id FROM knowledge_items WHERE {cond}", params
            )
            if row is None:
                return False
            await conn.execute(
                f"UPDATE knowledge_items SET labels=?,updated_at=? WHERE {cond}",
                (json.dumps(labels, ensure_ascii=False), generate_date(), *params),
            )
            await conn.commit()
        await self.sync_labels(item_id, str(row[0]), labels)
        return True

    async def update_metadata(
        self,
        item_id: str,
        owner_id: Optional[str],
        *,
        title: str,
        labels: List[str],
    ) -> bool:
        """Update user-editable metadata without touching stored content/source."""
        cond, params = _owner_filter(item_id, owner_id)
        async with open_db() as conn:
            row = await conn.fetchone(
                f"SELECT owner_id FROM knowledge_items WHERE {cond}", params
            )
            if row is None:
                return False
            await conn.execute(
                f"UPDATE knowledge_items SET title=?,labels=?,updated_at=? WHERE {cond}",
                (
                    title,
                    json.dumps(labels, ensure_ascii=False),
                    generate_date(),
                    *params,
                ),
            )
            await conn.commit()
        await self.sync_labels(item_id, str(row[0]), labels)
        return True
