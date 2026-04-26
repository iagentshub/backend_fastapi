<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/deployment.md">🇬🇧 Read in English</a>
</div>

<br>

# Despliegue

---

## Desarrollo local

```bash
git clone https://github.com/iagentshub/backend.git
cd backend
pip install -r requirements.txt

# Opcional: apuntar a un directorio de datos personalizado
export GAIA_DATA_DIR=/ruta/a/datos

python3 main.py
```

API disponible en `http://localhost:8765`. La recarga automática está activada por defecto (`GAIA_RELOAD=true`).

Credenciales por defecto: `admin` / `admin` — cámbialas en `data/settings.json` tras el primer inicio de sesión.

---

## Docker

```bash
docker build -t iagentshub-backend .
docker run -p 8765:8765 \
  -e GAIA_RELOAD=false \
  -e GAIA_AGENTS_SECRET=tu-secreto \
  -v /ruta/a/datos:/data \
  iagentshub-backend
```

---

## Vía iAgentsHub (recomendado)

Este servicio está diseñado para desplegarse como parte del stack de [iagentshub](https://github.com/iagentshub/iagentshub), que gestiona automáticamente la inicialización de datos, el aprovisionamiento de skills y el proxy inverso con un solo comando:

```bash
docker compose up --build
```

---

## Lista de verificación para producción

- Establecer `GAIA_RELOAD=false`
- Establecer `GAIA_AGENTS_SECRET` con un valor aleatorio fuerte (p.ej. `openssl rand -hex 32`)
- Establecer `GAIA_CORS_ORIGINS` con el origen exacto del frontend
- Montar `data/` como volumen persistente
- Cambiar la contraseña de admin por defecto
