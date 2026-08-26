<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/config.md">🇬🇧 Read in English</a>
</div>

<br>

# Configuración

La configuración de infraestructura y arranque se realiza mediante variables de
entorno. Los ajustes operativos que deben poder cambiarse sin reiniciar se
guardan desde el panel de administración. Ninguno requiere modificar código.

---

## Qué se puede configurar

| Ajuste | Descripción |
|---|---|
| Secreto de sesión | Clave usada para firmar los tokens de sesión. Obligatorio en producción. |
| Directorio de datos | Ruta de la base SQLite, ajustes, estado operativo y datos heredados. |
| Puerto y host | Dónde escucha el servidor. |
| Orígenes permitidos | Dominios desde los que se permite el acceso a la API. |
| Duración de sesión | Tiempo en horas que una sesión permanece activa. |
| Concurrencia LLM | Número máximo de llamadas simultáneas a proveedores por worker. |
| Escritura de logs | Tamaño de lote e intervalo de volcado del registro de actividad. |
| Orígenes internos de Ollama | Lista exacta de servidores locales/LAN a los que puede conectar el backend. |

## Ajustes administrables en caliente

`GET /api/settings/platform` y `PUT /api/settings/platform` permiten al admin
consultar y cambiar los ajustes operativos persistentes. Entre ellos está
`max_request_bytes`, el único límite global para el tamaño de las peticiones y
artefactos; `0` significa sin límite. No existe un límite diferente por cada
tipo de recurso.

`GET /api/settings/platform/public` expone a los clientes la parte no sensible
de esa configuración. Incluye `tool_runtimes`, el catálogo efectivo de Tools,
con sus códigos de API, extensiones, requisito de binario y destinos nativos.
Los clientes deben usar ese catálogo en vez de duplicar listas fijas.

### Límites estructurales de importación de directorios

Estos topes protegen la normalización y el catálogo frente a árboles
patológicos. No limitan los bytes ni sustituyen a `max_request_bytes`; aunque
este valga `0` —sin límite—, una ruta maliciosamente profunda sigue siendo
rechazada. Son configuración de arranque y requieren reiniciar el backend.

| Variable | Por defecto | Descripción |
|---|---:|---|
| `GAIA_DIRECTORY_IMPORT_MAX_FILES` | `5000` | Número máximo de archivos por directorio importado. |
| `GAIA_DIRECTORY_IMPORT_MAX_DEPTH` | `32` | Número máximo de segmentos de una ruta relativa. |
| `GAIA_DIRECTORY_IMPORT_MAX_PATH_LENGTH` | `500` | Longitud máxima de una ruta relativa normalizada. |

### Destinos internos de Ollama

| Variable | Por defecto | Descripción |
|---|---|---|
| `GAIA_OLLAMA_ALLOWED_ORIGINS` | `http://localhost:11434,http://127.0.0.1:11434,http://[::1]:11434,http://host.docker.internal:11434` | Orígenes internos exactos autorizados para Ollama, separados por comas. Los destinos públicos siguen pasando por validación y DNS fijado. |

La autorización incluye protocolo, host y puerto. Por ejemplo, permitir
`http://localhost:11434` no permite `http://localhost:5432`.
Esta variable solo abre excepciones para destinos internos. La opción oficial
`https://ollama.com` y las URLs personalizadas públicas se admiten sin
configuración adicional y conservan validación SSRF y DNS fijado.

## Auditoría de configuración al arrancar

Una variable que falta no rompe nada: apaga una función. Sin `GAIA_SMTP_HOST` los
correos de verificación no salen, y con un typo en `STRIPE_WEBHOOK_SECRET` el
servidor arranca igual y simplemente no cobra. Para que eso deje de pasar en
silencio, el arranque audita la configuración y escribe en el log **qué función
queda desactivada y por qué variable**.

Hay dos niveles:

| Nivel | Significado | Ejemplo |
|---|---|---|
| Aviso | Una función queda desactivada porque le falta configuración. Puede ser deliberado. | Sin `GAIA_SMTP_HOST` no se envía correo. |
| Error | La configuración se contradice: algo está activado y no puede funcionar. | Verificación de email activa sin servidor SMTP: nadie llega a poder entrar. |

| Variable | Por defecto | Descripción |
|---|---|---|
| `GAIA_STRICT_CONFIG` | *(desactivado)* | Ponlo a `true` para que los **errores** impidan el arranque en vez de solo avisar. |

Por defecto los errores no abortan: endurecerlo dejaría inarrancable una
instalación que hoy funciona degradada. En un despliegue de producción, activa
`GAIA_STRICT_CONFIG=true` una vez y el propio arranque avisa para siempre.

El mismo informe está en el panel de administración, en **Configuración →
Diagnóstico de configuración**, y en `GET /api/admin/config-audit`. Solo muestra
**nombres** de variable, nunca sus valores.

## Impuestos de las suscripciones

El precio anunciado es el **neto**: el checkout pide el país de facturación
antes de crear la suscripción y Stripe Tax suma encima el IVA que corresponda a
ese país. Una empresa de otro Estado miembro que declare un NIF-IVA válido paga
sin IVA, por inversión del sujeto pasivo.

El país no se puede pedir al final. `payment_behavior="default_incomplete"` emite
el borrador de factura en el mismo momento en que se crea la suscripción, así que
sin ubicación Stripe responde `customer_tax_location_invalid` y no hay alta. Por
eso `POST /api/billing/subscribe` exige `country` (ISO 3166-1 alfa-2) y acepta un
`tax_id` opcional, y devuelve `subtotal_cents`, `tax_cents` y `total_cents`
tomados de esa factura — que es lo que el cliente ve antes de pagar.

| Variable | Por defecto | Descripción |
|---|---|---|
| `STRIPE_TAX` | `true` | A `false` se cobra el neto y no se repercute IVA. |

Activarlo aquí no basta: en dashboard.stripe.com hacen falta **Tax habilitado**,
las obligaciones fiscales (*registrations*) del país declaradas, un `tax_code` en
el producto de `STRIPE_PRODUCT_SEATS` y `tax_behavior` en los precios del add-on
self-hosted, que son fijos y no se crean desde el código. Si falta alguna de esas
cuatro cosas, el alta falla al crear la suscripción; el arranque lo recuerda en el
informe de configuración.

Solo se registran NIF-IVA de la UE (`eu_vat`). Un cliente de fuera paga como
consumidor, que es correcto aunque sea una empresa; ampliarlo es añadir tipos en
`app/services/billing_tax.py`.

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
