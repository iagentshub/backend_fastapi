<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/config.md">🇬🇧 Read in English</a>
</div>

<br>

# Configuración

Toda la configuración se realiza mediante variables de entorno. Estas se establecen desde el orquestador de despliegue ([iAgentsHub](https://github.com/iagentshub/iAgents)) y no requieren modificar ningún fichero de código.

---

## Qué se puede configurar

| Ajuste | Descripción |
|---|---|
| Secreto de sesión | Clave usada para firmar los tokens de sesión. Obligatorio en producción. |
| Directorio de datos | Ruta donde se almacenan agentes, conexiones, skills y memoria. |
| Puerto y host | Dónde escucha el servidor. |
| Orígenes permitidos | Dominios desde los que se permite el acceso a la API. |
| Duración de sesión | Tiempo en horas que una sesión permanece activa. |
| Concurrencia LLM | Número máximo de llamadas simultáneas a proveedores por worker. |
| Escritura de logs | Tamaño de lote e intervalo de volcado del registro de actividad. |

## Escritura de logs

Los registros se escriben en la base de datos **por lotes**, no uno a uno: se
agrupan y se vuelcan en una sola transacción cuando se llena el lote o cuando
vence el intervalo, lo que ocurra antes.

| Variable | Por defecto | Descripción |
|---|---|---|
| `GAIA_LOG_BATCH_SIZE` | `50` | Registros por transacción. `1` restaura la escritura inmediata. |
| `GAIA_LOG_FLUSH_INTERVAL` | `1.0` | Segundos máximos que un registro puede esperar en memoria. |
| `GAIA_LOG_HEALTH` | *(desactivado)* | Ponlo a `1` para volver a registrar los health checks correctos. |

Las sondas de vida (`/api/health`) **no se registran cuando responden bien**: el
`HEALTHCHECK` del contenedor las dispara cada 30 s y con varios workers llenaban
la tabla de líneas idénticas. Si el health check **falla**, sí se registra.

Los mensajes de nivel `ERROR` se escriben siempre al instante, sin esperar al
lote. El visor de `/api/admin/logs` fuerza el volcado antes de consultar, así que
muestra siempre la actividad completa.

Baja `GAIA_LOG_BATCH_SIZE` solo si necesitas durabilidad estricta de cada línea:
con el valor por defecto, una caída abrupta del proceso puede perder como mucho
el último segundo de logs de diagnóstico.

## Concurrencia LLM

`GAIA_LLM_MAX_THREADS` controla el executor dedicado al streaming con proveedores
LLM. El valor predeterminado es `16` por worker. Al alcanzar el límite, los chats
nuevos reciben HTTP 429 con `Retry-After` en lugar de ocupar el executor general o
crecer en una cola sin límite. Auméntalo solo después de medir memoria, descriptores
de fichero y límites de los proveedores.

## Secreto de sesión

Debe generarse de forma aleatoria antes del primer arranque y no cambiarse mientras haya sesiones activas. Si no se configura, el sistema usa un valor almacenado en los datos de la plataforma — aceptable en desarrollo, no en producción.

Este secreto también actúa como clave maestra para cifrar las API keys almacenadas en la base de datos (derivada mediante PBKDF2-SHA256). **Cambiarlo después de haber guardado API keys hará que esas claves sean ilegibles** — los usuarios tendrán que volver a introducir sus credenciales.

Cuando eso ocurre, el fallo se ve: el recurso afectado se devuelve con `credentials_unreadable: true` (y `unreadable_fields` con los campos concretos), el cliente lo marca como *requiere atención* en el listado, y cualquier acción que fuese a usar la credencial —chat, test, importar modelos, sincronizar— responde con el código `credential_unreadable` en vez de mandar el valor cifrado al proveedor. El valor cifrado se conserva intacto: si se restaura el secreto correcto, las claves vuelven a leerse solas.
