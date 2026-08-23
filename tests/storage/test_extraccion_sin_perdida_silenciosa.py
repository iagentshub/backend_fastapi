"""Un documento que no entra entero tiene que decirlo.

Hasta 2026-08 `extract_document_text` recortaba a 500 000 caracteres y devolvía
un `str`: el texto llegaba a la ficha sin nada que distinguiera un documento
completo de uno al que le faltaba el 70 %. No había log, no había columna y la
interfaz enseñaba el `char_count` ya recortado como si fuera el del original —
que no se guarda, así que lo cortado no se podía recuperar de ninguna forma.

Estos tests fijan las dos mitades del arreglo: que la cota deje constancia, y
que ninguna ruta pueda volver a llamar a la variante que la pierde.
"""

from __future__ import annotations

import ast
import io
import zlib
from pathlib import Path

import pytest

from app.storage.knowledge import (
    MAX_EXTRACTED_CHARS,
    ExtractedDocument,
    _bounded,
    extract_document,
    extract_document_text,
)

_RUTAS = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"


def _pdf_de(n_paginas: int, lineas: int = 40) -> bytes:
    """PDF mínimo real, sin dependencias: n páginas del mismo texto."""
    linea = ("Lorem ipsum dolor sit amet consectetur adipiscing elit " * 2)[:80]
    cuerpo = "\n".join(f"({linea}) Tj 0 -14 Td" for _ in range(lineas))
    flujo = zlib.compress(f"BT /F1 11 Tf 40 780 Td\n{cuerpo}\nET".encode(), 9)

    salida = io.BytesIO()
    salida.write(b"%PDF-1.4\n")
    posiciones: dict[int, int] = {}
    numero = 0

    def objeto(datos: bytes) -> int:
        nonlocal numero
        numero += 1
        posiciones[numero] = salida.tell()
        salida.write(f"{numero} 0 obj\n".encode() + datos + b"\nendobj\n")
        return numero

    contenido = objeto(
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(flujo)
        + flujo
        + b"\nendstream"
    )
    fuente = objeto(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    padre = numero + n_paginas + 1
    hijos = [
        objeto(
            f"<< /Type /Page /Parent {padre} 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {fuente} 0 R >> >> "
            f"/Contents {contenido} 0 R >>".encode()
        )
        for _ in range(n_paginas)
    ]
    paginas = objeto(
        (
            "<< /Type /Pages /Count %d /Kids [%s] >>"
            % (n_paginas, " ".join(f"{h} 0 R" for h in hijos))
        ).encode()
    )
    catalogo = objeto(f"<< /Type /Catalog /Pages {paginas} 0 R >>".encode())

    xref = salida.tell()
    salida.write(f"xref\n0 {numero + 1}\n".encode())
    salida.write(b"0000000000 65535 f \n")
    for i in range(1, numero + 1):
        salida.write(f"{posiciones[i]:010d} 00000 n \n".encode())
    salida.write(
        f"trailer\n<< /Size {numero + 1} /Root {catalogo} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return salida.getvalue()


# ── La cota no puede morder documentos reales ────────────────────────────────


def test_un_pdf_largo_entra_entero():
    """400 páginas perdían el 69 % de su texto con la cota vieja de 500 000."""
    pytest.importorskip("pypdf")
    extraido = extract_document(_pdf_de(400), "manual.pdf", "application/pdf")
    assert not extraido.truncated
    assert extraido.lost_chars == 0
    # Con el límite anterior esto no llegaba ni a la mitad.
    assert len(extraido.text) > 500_000


def test_el_texto_completo_de_un_fichero_de_texto_no_se_recorta():
    crudo = ("línea de prueba\n" * 60_000).encode()
    extraido = extract_document(crudo, "notas.txt", "text/plain")
    assert extraido.text == crudo.decode()
    assert not extraido.truncated


# ── Cuando sí muerde, lo dice ────────────────────────────────────────────────


def test_pasar_la_cota_queda_anotado():
    extraido = _bounded("x" * (MAX_EXTRACTED_CHARS + 10))
    assert extraido.truncated
    assert extraido.reason == "max_chars"
    assert len(extraido.text) == MAX_EXTRACTED_CHARS
    assert extraido.source_chars == MAX_EXTRACTED_CHARS + 10
    assert extraido.lost_chars == 10


def test_lo_que_cabe_no_se_marca():
    extraido = _bounded("texto corto")
    assert not extraido.truncated
    assert extraido.reason == ""
    assert extraido.source_chars == len("texto corto")


def test_una_descarga_cortada_se_marca_aunque_el_texto_quepa(monkeypatch):
    """El HTML entra entero en la cota, pero venía de una descarga a medias."""
    from app.storage import knowledge as mod

    monkeypatch.setattr(
        mod,
        "_download_safe_url",
        lambda url: (b"<p>mitad de una pagina</p>", "text/html", True),
    )
    extraido = mod.fetch_url_document("https://example.com")
    assert extraido.truncated
    assert extraido.reason == "max_download_bytes"


def test_el_pdf_para_al_llegar_a_la_cota(monkeypatch):
    """Cortar al acumular, no al final: el resto de páginas ni se abre."""
    pytest.importorskip("pypdf")
    from app.storage import knowledge as mod

    monkeypatch.setattr(mod, "MAX_EXTRACTED_CHARS", 5_000)
    extraido = mod.extract_document(_pdf_de(200), "manual.pdf", "application/pdf")
    assert extraido.truncated
    assert extraido.reason == "max_chars"
    # source_chars estima el total del documento, no lo que se llegó a leer:
    # es lo que la interfaz enseña para explicar cuánto falta.
    assert extraido.source_chars > len(extraido.text)


# ── Nadie puede volver a perder los metadatos ────────────────────────────────


def test_ninguna_ruta_llama_a_la_variante_sin_metadatos():
    """`extract_document_text` devuelve un `str` y por eso pierde el aviso.

    Sigue existiendo porque hay sitios donde los metadatos dan igual, pero una
    ruta que la use vuelve a guardar un documento a medias como si estuviera
    entero, que es exactamente el fallo que esto cierra.
    """
    culpables = []
    for fichero in _RUTAS.rglob("*.py"):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Name) and nodo.id == "extract_document_text":
                culpables.append(f"{fichero.relative_to(_RUTAS.parent)}:{nodo.lineno}")
            elif (
                isinstance(nodo, ast.Attribute)
                and nodo.attr == "extract_document_text"
            ):
                culpables.append(f"{fichero.relative_to(_RUTAS.parent)}:{nodo.lineno}")
    assert culpables == [], (
        "Estas rutas extraen texto perdiendo el aviso de recorte; usa "
        f"`_extract_document` de knowledge/_shared.py: {culpables}"
    )


def test_ninguna_ruta_extrae_en_el_pool_de_bcrypt():
    """`asyncio.to_thread` deja el trabajo donde también corre bcrypt.

    Con `min(32, cpu + 4)` huecos, unas cuantas subidas grandes a la vez
    frenaban los logins sin que nada fallara ni se registrara: solo se
    esperaba. La extracción va por `run_document_blocking`, que tiene su propio
    pool acotado.
    """
    culpables = []
    for fichero in _RUTAS.rglob("*.py"):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            destino = nodo.func
            if not (
                isinstance(destino, ast.Attribute)
                and destino.attr == "to_thread"
                and nodo.args
            ):
                continue
            primero = nodo.args[0]
            nombre = getattr(primero, "id", None) or getattr(primero, "attr", None)
            if nombre in {
                "extract_document",
                "extract_document_text",
                "fetch_url_text",
                "fetch_url_document",
            }:
                culpables.append(f"{fichero.relative_to(_RUTAS.parent)}:{nodo.lineno}")
    assert culpables == [], (
        "Extracción de documentos en el executor por defecto (el de bcrypt); "
        f"usa `run_document_blocking`: {culpables}"
    )


def test_extract_document_text_sigue_devolviendo_solo_el_texto():
    crudo = b"contenido corto"
    assert extract_document_text(crudo, "a.txt") == "contenido corto"
    assert isinstance(extract_document(crudo, "a.txt"), ExtractedDocument)
