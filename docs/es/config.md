<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/config.md">🇬🇧 Read in English</a>
</div>

<br>

# Configuración

Todos los valores se leen al arrancar desde variables de entorno. No es necesario modificar ningún fichero de configuración en la mayoría de despliegues.

| Variable | Valor por defecto | Obligatoria | Descripción |
|---|---|---|---|
| `GAIA_DATA_DIR` | `./data` | No | Ruta absoluta al directorio de datos |
| `GAIA_HOST` | `0.0.0.0` | No | Dirección de escucha |
| `GAIA_PORT` | `8765` | No | Puerto de escucha |
| `GAIA_RELOAD` | `true` | No | Activar recarga automática. Poner `false` en producción |
| `GAIA_AGENTS_SECRET` | — | **Sí (prod)** | Secreto para firmar JWT. Si no se define, usa `settings.json#jwt_secret` |
| `GAIA_CORS_ORIGINS` | `*` | No | Lista de orígenes permitidos separados por comas |
| `GAIA_JWT_EXPIRE_HOURS` | `12` | No | Tiempo de vida del token en horas |

---

## settings.json

Ubicado en `$GAIA_DATA_DIR/settings.json`. Almacena la cuenta de admin y un secreto JWT de respaldo.

```json
{
  "admin_username": "admin",
  "admin_password_hash": "$2b$12$...",
  "jwt_secret": "cámbialo-en-producción"
}
```

> `GAIA_AGENTS_SECRET` tiene prioridad sobre `jwt_secret` en `settings.json`. Usa siempre la variable de entorno en producción.

---

## CORS

Para un único origen de frontend:

```bash
GAIA_CORS_ORIGINS=https://app.ejemplo.com
```

Para múltiples orígenes:

```bash
GAIA_CORS_ORIGINS=https://app.ejemplo.com,http://localhost:3000
```

Usa `*` solo en desarrollo local.
