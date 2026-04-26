<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/deployment.md">🇬🇧 Read in English</a>
</div>

<br>

# Despliegue

---

## Vía iAgentsHub (recomendado)

Este servicio está diseñado para desplegarse como parte del stack de [iAgentsHub](https://github.com/iagentshub/iagentshub), que gestiona automáticamente la inicialización, configuración y proxy inverso con un solo comando:

```bash
docker compose up --build
```

---

## Docker

```bash
docker build -t iagentshub-backend .
docker run -p 8765:8765 \
  -e GAIA_RELOAD=false \
  -e GAIA_AGENTS_SECRET=tu-secreto-seguro \
  -e GAIA_ADMIN_PASSWORD=tu-password-admin \
  -e GAIA_GOOGLE_CLIENT_ID=... \
  -e GAIA_GOOGLE_CLIENT_SECRET=... \
  -e GAIA_GOOGLE_REDIRECT_URI=https://tu-dominio.com/api/auth/google/callback \
  -e GAIA_FRONTEND_URL=https://tu-dominio.com \
  -v /ruta/a/datos:/data \
  iagentshub-backend
```

---

## Desarrollo local

```bash
git clone https://github.com/iagentshub/backend.git
cd backend
pip install -r requirements.txt
export GAIA_ADMIN_PASSWORD=admin
export GAIA_AGENTS_SECRET=cualquier-valor-para-desarrollo
python3 main.py
```

API disponible en `http://localhost:8765`.

---

## Lista de verificación para producción

- [ ] Definir `GAIA_AGENTS_SECRET` con un valor aleatorio seguro (`openssl rand -hex 32`)
- [ ] Definir `GAIA_ADMIN_PASSWORD` para acceso de emergencia
- [ ] Configurar las variables de Google OAuth
- [ ] Establecer `GAIA_CORS_ORIGINS` con el dominio exacto del frontend
- [ ] Establecer `GAIA_RELOAD=false`
- [ ] Montar el directorio de datos en un volumen persistente
