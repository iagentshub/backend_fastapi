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

# Usuario sin privilegios. El contenedor SIGUE arrancando como root a
# propósito: el entrypoint necesita ceder /data antes de bajar privilegios,
# porque en una instalación ya existente ese volumen es de root. Ver
# docker-entrypoint.sh para el detalle.
# El sed y el chmod no sobran: el repo se clona también en Windows, que con
# core.autocrlf deja el fichero con CRLF —el shebang queda "#!/bin/sh\r" y el
# contenedor muere con "no such file or directory" señalando un intérprete que
# sí existe— y no conserva el bit de ejecución.
RUN useradd --system --create-home --uid 1000 gaia \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R gaia:gaia /app

EXPOSE 8765

# Producción: sin reload automático
ENV GAIA_RELOAD=false

# Sin esto, DATA_DIR cae a su valor por defecto —hermano del repo, que dentro
# del contenedor es /iAgents— y la imagen escribe fuera del volumen: los datos
# se pierden al recrear el contenedor, sin un solo error. Los composes ya lo
# fijaban, así que solo se notaba al arrancar la imagen a mano; y no se notaba
# nada mientras el proceso era root, porque podía crear /iAgents sin quejarse.
ENV GAIA_DATA_DIR=/data

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
