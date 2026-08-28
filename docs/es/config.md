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

## Notificaciones push

Los avisos de la campana pueden además saltar como notificación del sistema
—fuera de la aplicación, con la pestaña cerrada— usando **Web Push**. Hace
falta un par de claves VAPID, que identifica a esta instalación ante el
servicio push de cada navegador. Este comando imprime las tres variables listas
para copiar:

```bash
python - <<'EOF'
import base64
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01

v = Vapid01(); v.generate_keys()
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
print("GAIA_VAPID_PUBLIC_KEY=" + b64(v.public_key.public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)))
print("GAIA_VAPID_PRIVATE_KEY=" + b64(
    v.private_key.private_numbers().private_value.to_bytes(32, "big")))
print("GAIA_VAPID_SUBJECT=mailto:CAMBIA@ESTO.com")
EOF
```

`python -m py_vapid --gen` también sirve, pero **no da las claves en el formato
de las variables**: escribe `private_key.pem` y `public_key.pem`. En ese caso
pega el contenido del PEM privado en `GAIA_VAPID_PRIVATE_KEY` y la salida de
`python -m py_vapid --applicationServerKey` en la pública. El backend acepta las
dos formas de la clave privada, con los saltos de línea escapados o sin escapar.

| Variable | Por defecto | Descripción |
|---|---|---|
| `GAIA_VAPID_PUBLIC_KEY` | *(vacío)* | Clave pública. La recibe el navegador al suscribirse. |
| `GAIA_VAPID_PRIVATE_KEY` | *(vacío)* | Clave privada con la que se firma cada envío. |
| `GAIA_VAPID_SUBJECT` | *(vacío)* | Contacto del operador (`mailto:` o `https://`). Lo exige el RFC 8292 y algunos servicios rechazan los envíos que no lo llevan. |

Sin estas variables el push queda desactivado y la aplicación no ofrece el
interruptor; la campana y el correo siguen funcionando igual.

**El par no se rota a la ligera.** El navegador comprueba que la clave del
envío es la misma con la que se suscribió, así que cambiarla invalida de golpe
todas las suscripciones existentes y cada usuario tiene que volver a activarlo.

### Retención de los avisos

Los avisos se barren solos, con **dos ventanas** distintas: una leída ya cumplió
su función, mientras que una sin leer puede ser lo único que le quede al usuario
de que aquello ocurrió —la invitación que la originó desaparece de
`group_invitations` en cuanto se acepta—.

| Ajuste (panel de admin) | Por defecto | Qué barre |
|---|---|---|
| `notification_retention_days` | 90 | Avisos **leídos** más antiguos que eso |
| `notification_unread_retention_days` | 365 | Avisos **sin leer** más antiguos que eso |

| Variable | Por defecto | Descripción |
|---|---|---|
| `GAIA_NOTIFICATION_PURGE_HOURS` | 24 | Cada cuánto se pasa la escoba. No es la política: subirlo deja más basura entre pasadas, no cambia lo que ve un usuario. |

Las suscripciones push no necesitan purga: el servicio push responde 404 o 410
cuando el navegador ya las tiró y la fila se borra en ese momento. Es la propia
entrega la que limpia.

### Qué recibe cada usuario

Dos niveles, y el general manda sobre el particular:

1. **Interruptor por canal** (`notify_email`, `notify_push`): apaga el canal
   entero.
2. **Interruptor por categoría y canal**: afina dentro del canal encendido.

Las categorías las declara `app/models/notification_kinds.py` y las **publica el
servidor** en `/api/settings`; el cliente pinta lo que reciba. Así añadir un tipo
de evento no deja al cliente con un interruptor que falta.
`tests/api/test_notification_kinds.py` comprueba que todo tipo emitido o con
plantilla de correo pertenece a una categoría: uno huérfano ignoraría las
preferencias del usuario en silencio.

**La campana no se apaga.** Es el registro de lo que pasó, no una interrupción,
y sin ella el usuario se quedaría sin forma de enterarse de nada.

### Reintentos del push

Un envío se reintenta hasta **3 veces** con retroceso exponencial (1 s, 2 s), y
solo ante lo que puede arreglarse solo: 408, 429 y los 5xx. Un 400, 401 o 403 es
culpa del mensaje o de la firma y repetirlo da el mismo error. Un 404 o un 410
significan que la suscripción ya no existe, así que se borra en vez de
reintentarse.

Si el servicio manda `Retry-After`, se respeta —es él quien sabe cuándo volver, y
adelantarse convierte un 429 en un bloqueo—, con un techo de 60 segundos: un
aviso no vale tener una tarea dormida media hora, y además sigue en la campana.

### Qué cubre y qué no

| Dónde | ¿Salta con la aplicación cerrada? |
|---|---|
| Android (Chrome) y escritorio | Sí, mientras el navegador siga vivo en segundo plano |
| macOS Safari | Sí |
| iPhone, pestaña normal de Safari | **No.** Apple no lo permite |
| iPhone, aplicación añadida a la pantalla de inicio | Sí, desde iOS 16.4 |

Ese último caso es el único hueco y no depende de la configuración: la
aplicación detecta a quien entra desde un iPhone sin haberla instalado y le
explica el paso que le falta.

Android e iOS **nativos** usarían FCM y APNs, que son otro canal. La tabla
`push_subscriptions` ya distingue el tipo en su columna `kind`, así que
añadirlos el día que se publiquen las aplicaciones no toca ni el esquema ni los
productores de avisos.

## Secreto de sesión

Debe generarse de forma aleatoria antes del primer arranque y no cambiarse mientras haya sesiones activas. Si no se configura, el sistema usa un valor almacenado en los datos de la plataforma — aceptable en desarrollo, no en producción.

Este secreto también actúa como clave maestra para cifrar las API keys almacenadas en la base de datos (derivada mediante PBKDF2-SHA256). **Cambiarlo después de haber guardado API keys hará que esas claves sean ilegibles** — los usuarios tendrán que volver a introducir sus credenciales.

Cuando eso ocurre, el fallo se ve: el recurso afectado se devuelve con `credentials_unreadable: true` (y `unreadable_fields` con los campos concretos), el cliente lo marca como *requiere atención* en el listado, y cualquier acción que fuese a usar la credencial —chat, test, importar modelos, sincronizar— responde con el código `credential_unreadable` en vez de mandar el valor cifrado al proveedor. El valor cifrado se conserva intacto: si se restaura el secreto correcto, las claves vuelven a leerse solas.
