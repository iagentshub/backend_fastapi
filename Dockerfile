# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.lock

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# OCR local para convertir capturas y fotografias en conocimiento textual.
# Los idiomas se limitan a los que ofrece actualmente la aplicacion.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

# Versión de build (formato YYYYMMDDHHMMSS, UTC) — la inyecta el workflow de
# CI vía --build-arg. "dev" en builds locales sin el arg.
ARG GAIA_VERSION=dev
ENV GAIA_VERSION=$GAIA_VERSION
LABEL org.iagentshub.version=$GAIA_VERSION
LABEL org.opencontainers.image.source="https://github.com/iagentshub/backend_fastapi"

WORKDIR /app

# Copiar dependencias instaladas
COPY --from=builder /install /usr/local

# Copiar código fuente (data/ queda fuera gracias a .dockerignore)
COPY . .

# Usuario sin privilegios, pero el contenedor SIGUE arrancando como root a
# propósito. El sed y el chmod tampoco sobran: defienden del clon hecho en
# Windows. Ver docs/adr/002-el-contenedor-arranca-como-root.md
RUN useradd --system --create-home --uid 1000 gaia \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R gaia:gaia /app

EXPOSE 8765

# Producción: sin reload automático
ENV GAIA_RELOAD=false

# Sin esto la imagen escribe fuera del volumen y los datos se pierden al
# recrear el contenedor, sin un solo error.
# Ver docs/adr/003-gaia-data-dir-y-healthcheck.md
ENV GAIA_DATA_DIR=/data

# start-period generoso a propósito: un arranque limpio migra el esquema entero
# antes de responder. Ver docs/adr/003-gaia-data-dir-y-healthcheck.md
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
