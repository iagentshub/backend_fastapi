# 011 · Un solo límite de tamaño de petición, y lo pone el administrador

- **Fecha**: 2026-08-19
- **Estado**: aceptada
- **Afecta a**: `app/middleware/body_limit.py`, `app/config/session.py`,
  `app/config/startup_checks.py`, `app/api/routes/settings.py`,
  `app/api/routes/auth/login.py`, `app/api/app.py`,
  `frontend_react/nginx.react.conf`,
  `app_flutter/lib/shared/state/upload_limits.dart` y los tres sitios que
  validaban tamaño en el cliente

## Contexto

Un fichero que subía a Knowledge atravesaba **cuatro controles de tamaño y los
cuatro decían cosas distintas**:

| Dónde | Cuánto |
|---|---|
| Flutter (`knowledgePackMaxFileBytes`) | 10 MB por fichero, 50 MB por tanda |
| `BodySizeLimitMiddleware` (`GAIA_BODY_MAX_BYTES`) | 2 MB, con un override de 11 MB para `/api/auth/me/avatar` |
| `upload_avatar` (`_MAX_AVATAR_BYTES`) | 10 MB |
| nginx | 1 MB — **no lo declaraba nadie**, es el valor por defecto de `client_max_body_size` |

Como nginx va delante, el límite efectivo en producción era **1 MB**, el más
bajo de los cuatro, y ninguno de los otros tres llegaba a ejecutarse nunca. Lo
que veía el usuario era lo peor de todas las opciones: elegía un PDF de 4 MB
porque la interfaz se lo permitía y recibía la **página HTML de error de
nginx**, no el `APIError` `payload_too_large` con `limit_bytes` dentro que el
backend fabrica con todo cuidado. Flutter intentaba leer ese HTML como JSON y
fallaba por otro sitio, así que el mensaje final ni siquiera mencionaba el
tamaño.

Dos consecuencias más, ambas medibles: el override de 11 MB del avatar era
**código muerto** —nginx mataba la petición once veces antes—, y el tope de 10
MB del propio handler no había sido cierto ni un día. El origen se ve en el
nombre de la variable: `BODY_MAX_BYTES` se dimensionó para cuerpos JSON, donde
2 MB es generoso, y las subidas de ficheros llegaron después y heredaron ese
número sin que nadie lo revisara.

## Decisión

**Un solo número, decidido por el administrador, y por defecto no hay ninguno.**

- `max_request_bytes` es un ajuste de plataforma más (`settings.json`, editable
  en Admin · Configuración · Subidas). **0 significa sin límite** y es el
  valor por defecto: quien despliega esto en su casa sube el pack que quiera
  sin tocar nada.
- Se aplica a **toda petición**, no solo a las subidas. Un segundo número para
  los cuerpos JSON es exactamente cómo empezó esto.
- `BodySizeLimitMiddleware` lo lee en cada petición, con un caché que se
  invalida al guardar (mismo patrón que `billing_enabled` en
  `licenses.py`): fijarlo al construir el middleware lo congelaría en el valor
  del arranque y el panel no cambiaría nada hasta reiniciar.
- `GAIA_BODY_MAX_BYTES` sobrevive como valor de partida cuando el
  administrador no ha tocado nada, y su default pasa de 2 MB a 0.
- **nginx no impone techo propio** (`client_max_body_size 0`). Su 413 es HTML y
  no lo puede interpretar ningún cliente; el rechazo tiene que producirlo el
  backend, que responde JSON con el límite dentro.
- El cliente lo lee de `/api/settings/platform/public`, que ya consulta al
  arrancar, y lo guarda en `UploadLimits`. No valida por su cuenta: solo evita
  el viaje inútil y pone el número real en el mensaje.

## Alternativas descartadas

- **Elegir un número fijo y escribirlo en los tres sitios** (lo que proponía el
  informe: 10 MB). Arregla la incoherencia de hoy y deja intacto el mecanismo
  que la produjo — tres copias mantenidas a mano en tres lenguajes que se
  despliegan por separado.
- **Un límite solo para las subidas, dejando 2 MB para el resto.** Más seguro
  sobre el papel, pero vuelve a haber dos números que pueden contradecirse y el
  administrador tendría que entender cuál le toca a cada petición.
- **Un techo alto en nginx (512 MB) por si el ajuste se pone absurdo.** Un
  administrador que configure por encima de ese número vuelve a ver el 413 en
  HTML, que es el fallo original con otra cifra.

## Consecuencias

- **Sin límite es el default, y eso amplía la superficie de abuso**: los
  handlers hacen `await file.read()` entero en memoria, así que una subida
  grande repetida ocupa lo que quiera quien la envía. La auditoría de arranque
  lo dice en cada arranque (`body_limit`, severidad *warning*) y el panel de
  admin lo enseña, para que sea una decisión y no un descuido.
- El avatar ya no tiene tope propio: su `avatar_too_large` desaparece del
  catálogo de errores. El formato sí se sigue comprobando.
- nginx tiene que redesplegarse para que esto surta efecto. Mientras siga
  sirviéndose la imagen anterior, el límite real es 1 MB pase lo que pase en el
  panel — es el único paso de esta decisión que no es config en caliente.
