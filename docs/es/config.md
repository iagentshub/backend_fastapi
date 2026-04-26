<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/config.md">🇬🇧 Read in English</a>
</div>

<br>

# Configuración

Toda la configuración se realiza mediante variables de entorno. No es necesario modificar ningún fichero de código.

---

## Configuración esencial

| Variable | Descripción | Obligatoria |
|---|---|---|
| `GAIA_AGENTS_SECRET` | Clave secreta para proteger las sesiones de usuario. Usar un valor aleatorio y largo. | **Sí** |
| `GAIA_ADMIN_PASSWORD` | Contraseña del administrador. Permite acceso de emergencia cuando el login con Google no está disponible. | Recomendada |

---

## Acceso con Google

Para activar el inicio de sesión con Google, configura estas variables con las credenciales obtenidas en [Google Cloud Console](https://console.cloud.google.com/):

| Variable | Descripción |
|---|---|
| `GAIA_GOOGLE_CLIENT_ID` | ID de cliente de la aplicación Google |
| `GAIA_GOOGLE_CLIENT_SECRET` | Secreto de cliente de la aplicación Google |
| `GAIA_GOOGLE_REDIRECT_URI` | URL de retorno tras el login (p.ej. `https://tu-dominio.com/api/auth/google/callback`) |
| `GAIA_FRONTEND_URL` | URL del frontend, para redirigir al usuario tras iniciar sesión |

### Restricción de acceso (opcional)

Por defecto, cualquier cuenta de Google puede acceder. Para limitar el acceso:

| Variable | Descripción | Ejemplo |
|---|---|---|
| `GAIA_ALLOWED_EMAILS` | Lista de correos permitidos, separados por comas | `ana@empresa.com,juan@empresa.com` |
| `GAIA_ALLOWED_DOMAINS` | Lista de dominios permitidos, separados por comas | `empresa.com` |

---

## Configuración del servidor

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `GAIA_DATA_DIR` | `./data` | Directorio donde se almacenan agentes, conexiones, skills y memoria |
| `GAIA_HOST` | `0.0.0.0` | Dirección de escucha del servidor |
| `GAIA_PORT` | `8765` | Puerto del servidor |
| `GAIA_RELOAD` | `true` | Recarga automática al detectar cambios. Desactivar en producción (`false`) |
| `GAIA_CORS_ORIGINS` | `*` | Orígenes permitidos para acceder a la API (p.ej. `https://app.tudominio.com`) |
| `GAIA_JWT_EXPIRE_HOURS` | `12` | Duración de la sesión en horas |

---

## Lista de verificación para producción

- [ ] Definir `GAIA_AGENTS_SECRET` con un valor aleatorio seguro
- [ ] Definir `GAIA_ADMIN_PASSWORD` para acceso de emergencia
- [ ] Configurar las variables de Google OAuth
- [ ] Establecer `GAIA_CORS_ORIGINS` con el dominio exacto del frontend
- [ ] Establecer `GAIA_RELOAD=false`
- [ ] Montar `GAIA_DATA_DIR` en un volumen persistente
