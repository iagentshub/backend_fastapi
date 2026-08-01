# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.lock

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

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

EXPOSE 8765

# Producción: sin reload automático
ENV GAIA_RELOAD=false

CMD ["python", "main.py"]
