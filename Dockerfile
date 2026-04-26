# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copiar dependencias instaladas
COPY --from=builder /install /usr/local

# Copiar código fuente (data/ queda fuera gracias a .dockerignore)
COPY . .

EXPOSE 8765

# Producción: sin reload automático
ENV GAIA_RELOAD=false

CMD ["python", "main.py"]
