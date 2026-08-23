# 013 · La extracción de documentos no pierde texto en silencio

- **Fecha**: 2026-08-23
- **Estado**: aceptada; completa la
  [011](011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md) en las rutas de
  conocimiento, que aquella no llegó a revisar
- **Afecta a**: `app/storage/knowledge.py`, `app/storage/knowledge_packs.py`,
  `app/services/document_executor.py`, `app/models/knowledge_item.py`,
  `app/sql/schema/knowledge_items.sql`, `app/sql/queries/knowledge*.sql`,
  las cuatro rutas de `app/api/routes/knowledge/`, y en `app_flutter`
  `lib/models/knowledge/knowledge_models.dart` y
  `lib/features/knowledge/cards/knowledge_sections.dart`

## Contexto

`extract_document_text` terminaba sus siete salidas en `[:MAX_CONTENT]`, con
`MAX_CONTENT = 500_000`. No era la cota de la rama de PDF: era la de todas —
PDF, OCR de imagen, fichero de texto y descarga de URL, que además se cortaba
antes por bytes con `_MAX_DOWNLOAD_BYTES = 2 MB`.

Medido sobre la función real, un PDF de **62 KB** con 400 páginas devolvía
500 000 de 1 638 000 caracteres: **se perdía el 69,5 %**, y el corte caía por la
página 122. El límite de subida de esa ruta eran 10 MB, así que no protegía de
nada: al techo se llegaba con un fichero doscientas veces más pequeño. Un
manual, una normativa o un contrato de más de ~120 páginas entraba ya recortado.

Lo que convertía esto en un fallo y no en una limitación es que **no dejaba
rastro de ningún tipo**:

- No había log. El `except Exception` ancho de los cuatro llamadores estaba bien
  justificado y anotaba los fallos, pero aquí no fallaba nada: la función
  devolvía menos texto y volvía con normalidad.
- No había columna. `char_count` guardaba el número **ya recortado**, así que ni
  siquiera quedaba la huella de que hubiera habido más.
- No había interfaz. Flutter pintaba `'${item.charCount} chars'` como si fuera
  el tamaño del documento.
- **Y el original no se guarda.** De él quedan `checksum` y `size_bytes`, nunca
  los bytes. Lo cortado no se podía recuperar de ninguna forma.

El resultado práctico: el usuario subía un documento, la ficha aparecía sin
avisar de nada, y el agente respondía sobre un texto al que le faltaba el 70 %
sin que ni el usuario ni el agente pudieran saberlo. El propio backend ya hacía
lo contrario en el chat, donde recortar el contexto emite un
`context_warning` / `context_truncated` que llega al usuario.

Un segundo problema vivía en la misma función. La rama de imagen tenía cuatro
cotas (`MAX_IMAGE_PIXELS`, `verify()`, `thumbnail`, `timeout=30`) y la de PDF
ninguna: sin tope de páginas, sin reloj, y con el recorte aplicado **al final**,
de modo que un documento de diez mil páginas se procesaba entero para quedarse
con las primeras ciento veinte. Y las cuatro rutas extraían con
`asyncio.to_thread`, es decir en el **executor por defecto de asyncio**, que es
donde también corre `bcrypt`: unas cuantas subidas grandes simultáneas llenaban
sus `min(32, cpu + 4)` huecos y los logins se paraban detrás, sin excepción, sin
log y sin nada que relacionara una cosa con la otra.

## Decisión

**1. La cota deja de ser el recorte normal y pasa a ser defensa del proceso.**
`MAX_EXTRACTED_CHARS = 20_000_000` — unas 5 000 páginas de libro, por encima de
cualquier documento legítimo. Lo único que defiende es la memoria; no es
configurable, por las mismas razones que las cotas de la rama de imagen.
`_MAX_DOWNLOAD_BYTES` sube de 2 MB a 20 MB.

**2. Cuando la cota muerde, se dice.** `extract_document` devuelve un
`ExtractedDocument` con `text`, `truncated`, `source_chars` y `reason`
(`max_chars`, `timeout`, `max_download_bytes`). Eso viaja hasta tres columnas
nuevas de `knowledge_items` — `source_char_count`, `content_truncated`,
`truncation_reason` — y desde ahí a la API, y de la API a un `AttentionBadge`
en la ficha cuyo tooltip dice cuánto falta y por qué.

`extract_document_text` sigue existiendo devolviendo solo el `str`, porque hay
sitios donde los metadatos dan igual. **Ninguna ruta puede usarla**: perder el
`truncated` es exactamente cómo un documento a medias acababa guardado como si
estuviera entero.

**3. El PDF se corta mientras se acumula, no al final**, y con reloj
(`_PDF_DEADLINE_SECONDS = 120`) mirado entre páginas. Dentro de una página no
hay forma de interrumpir a pypdf desde Python, pero entre dos sí.

**4. La extracción sale del pool de bcrypt.** `app/services/document_executor.py`
tiene su propio `ThreadPoolExecutor` acotado (`GAIA_DOCUMENT_MAX_THREADS`, 4 por
defecto). A diferencia de `LLMExecutor` sí encola: una subida que espera turno es
razonable; rechazarla por capacidad cambiaría el contrato de la API. Lo que no
puede es esperar donde espera un login.

**5. Y de paso, el techo de subida vuelve a ser uno solo.** La 011 decidió que
el tamaño lo decide `max_request_bytes` desde el panel, pero no revisó
conocimiento: quedaban 10 MB por documento en `items.py`, 10 MB por fichero de
pack y 50 MB por pack, todos por **debajo** del panel. Con el administrador en
«sin límite», Flutter dejaba elegir el fichero, nginx y el middleware lo dejaban
pasar, y era un literal de una ruta el que respondía 413 con un número que no
aparece en ninguna pantalla. Los tres desaparecen. Se queda
`_PACK_SESSION_MAX_TOTAL_BYTES`, que acumula entre varias peticiones y por eso
ningún middleware puede verlo.

## Alternativas descartadas

**Guardar el fichero original.** Resolvería la irrecuperabilidad de raíz, pero
cambia el modelo de almacenamiento entero (blobs, RGPD, packs, exportación) para
un caso que con la cota nueva deja de darse en documentos reales.

**Trocear el contenido en varias filas.** Permite texto ilimitado de verdad, a
cambio de tocar todas las consultas, el borrado RGPD, la exportación y el
listado. Con `TEXT` de SQLite y PostgreSQL llegando a 1 GB, la columna no es el
límite: el límite es la memoria del proceso, y trocear no la ahorra.

**Hacer la cota configurable desde el panel.** Es una defensa del proceso, no una
decisión de producto: mal puesta cambia un fallo silencioso por otro.

**Reutilizar `LLMExecutor`.** Su semántica es no encolar y lanzar
`LLMCapacityError`; aplicada a subidas, convertiría una espera en un 503.

## Consecuencias

- Un documento largo entra entero. Lo que antes se perdía a partir de la página
  ~122 ahora se importa completo.
- Cuando algo no cabe, se ve: badge en la ficha, motivo y cifras en el tooltip,
  `flog.warning` en el log y tres columnas en la base.
- Las filas anteriores no se pueden reparar — el texto que falta no está en
  ninguna parte. La migración 36 marca las que tocaron el techo exacto de
  500 000, que es la única señal que queda.
- Subir documentos deja de poder ralentizar los logins.
- Un PDF patológico ocupa un hilo del pool de documentos hasta 120 s, no
  indefinidamente. **El reloj no interrumpe de verdad**: Python no mata un hilo,
  así que se deja de esperar la página en curso, no de pagarla. Acotarla del todo
  exige un subproceso.
- `source_chars` de un PDF cortado es una **estimación** (lo leído extrapolado al
  total de páginas), no un dato exacto: el resto no se abrió. Es una cifra para
  enseñar, no para calcular.
- Con el panel en «sin límite» ya no hay ningún techo de subida en conocimiento.
  Es lo que la 011 decidió, y por eso «sin límite» sale como aviso en la
  auditoría de arranque.

Las guardas están en `tests/storage/test_extraccion_sin_perdida_silenciosa.py`
(la cota deja constancia; ninguna ruta usa la variante sin metadatos ni extrae
en el pool por defecto) y en `tests/api/test_routes_knowledge.py` (el aviso
llega a la ficha y al listado).
