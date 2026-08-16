# 006 · Anti-CSRF en dos capas: Origin verificado y token derivado del JWT

- **Fecha**: 2026-08-17
- **Estado**: aceptada
- **Afecta a**: `backend_fastapi/app/middleware/csrf.py`,
  `backend_fastapi/app/auth/cookies.py`,
  `backend_fastapi/app/auth/passwords.py`,
  `backend_fastapi/app/utils/net.py`,
  `backend_fastapi/app/config/session.py`,
  `frontend_react/src/api/client.ts`,
  `app_flutter/lib/core/network/csrf_token.dart`,
  `app_flutter/lib/core/network/api_client.dart`

## Contexto

La sesión web viaja en la cookie `ga_token`, y el navegador la adjunta a
cualquier petición dirigida al dominio **venga de donde venga**. Una página de
terceros que haga `POST /api/agents/…` desde el navegador de alguien con sesión
abierta obtiene una petición perfectamente autenticada sin que el usuario haga
clic en nada. Eso es CSRF.

Lo único que lo impedía era `SameSite=Lax`. Es una defensa correcta y sigue
puesta, pero tenía tres problemas como **única** capa:

1. **Vive en el navegador del visitante, no en nuestro servidor.** No podemos
   comprobar que se haya aplicado.
2. **Los subdominios cuentan como «el mismo sitio».** Desde un
   `blog.iagentshub.com` comprometido, `Lax` sí manda la cookie. Es el
   escenario realista, no el teórico.
3. **No había nada detrás.** Ni token sincronizador, ni verificación de
   `Origin`, ni `Referer`.

Restricción de partida: el backend atiende a cuatro clientes muy distintos y
**ningún cambio es atómico entre los cinco clones**. React (`credentials:
"same-origin"`), Flutter web (`BrowserClient..withCredentials`), Flutter nativo
(que manda `Cookie: ga_token=…` a mano y **no manda `Origin`**), la extensión de
VS Code (que autentica con `Authorization: Bearer` y un PAT) y el webhook de
Stripe. Una puerta que no distinga entre ellos los rompe.

## Decisión

Dos capas independientes en `CsrfMiddleware`, cada una con su interruptor de
tres niveles (`enforce | log | off`).

### Capa 1 — verificación de `Origin`/`Referer`

El navegador escribe `Origin` y el JavaScript de una página no puede
falsificarla. Se compara contra `CORS_ORIGINS` **y además** contra el propio
host al que iba dirigida la petición (`request_origin()` en `app/utils/net.py`,
que lee `X-Forwarded-Host`/`X-Forwarded-Proto` solo desde `TRUSTED_PROXIES`,
mismo criterio que `client_ip()`).

Aceptar el propio host no es una fuga: en una petición cross-site el `Host` lo
escribe el navegador apuntando a *nuestro* dominio, el atacante no lo controla.
Lo que evita es el modo de fallo que el propio informe advertía — un
`GAIA_CORS_ORIGINS` desactualizado rechazando tráfico legítimo con un error
indistinguible desde el cliente. Es real aquí: en producción los dos frontends
son *same-origin*, así que CORS no se ejercita nunca y esa variable puede estar
mal sin que nadie lo note.

Se aplica también a peticiones **anónimas**: es gratis y bloquea de paso el
*login CSRF* (forzar a la víctima a iniciar sesión con la cuenta del atacante
para que su actividad posterior caiga en ella).

### Capa 2 — token double-submit derivado del JWT

`ga_csrf = base64url(HMAC-SHA256(secreto_de_firma, ga_token))`, en una cookie
sin `HttpOnly` que el cliente reenvía en `X-CSRF-Token`.

**Derivado, no aleatorio-y-guardado.** Un token aleatorio en una tabla habría
exigido estado, expiración y una consulta por petición. Este no: es una función
pura del JWT y muere con él.

Y sobre todo, **resiste el *cookie tossing***, que es donde el double-submit
clásico fracasa. Un subdominio comprometido puede sobreescribir la cookie del
token *y* mandar ese mismo valor en la cabecera; un servidor que se limite a
comparar cookie contra cabecera lo da por bueno. Aquí el servidor recalcula el
HMAC desde el `ga_token` de la víctima, así que un valor inyectado no cuadra.

Tercera consecuencia, la que hace desplegable la capa: como el token se
recalcula, el middleware **repone la cookie en cualquier respuesta a un método
seguro** cuando hay sesión y falta o no cuadra. Las sesiones abiertas antes del
despliegue se curan solas en la primera navegación y nadie queda deslogueado al
subir el modo a `enforce`.

### Las dos exenciones

- **`Authorization: Bearer` presente** → se salta todo. Un PAT no es una
  credencial ambiental: el navegador no lo adjunta solo. Deja indemnes a la
  extensión de VS Code y a los scripts.
- **Sin `Origin` ni `Referer`** → la capa 1 deja pasar. A un navegador no se le
  puede obligar a omitir `Origin` en un POST, así que su ausencia identifica a
  un cliente que no es atacable por esta vía: Flutter nativo, `curl`, el
  webhook de Stripe.

### Auditoría de métodos `GET` con efectos

De las 106 rutas `GET` del contrato (`tests/api/contrato_rutas.txt`), la
**única** con efectos secundarios es `GET /api/auth/verify`: verifica el email y
abre sesión. Tiene que seguir siendo `GET` porque es un enlace de correo, y
llega como navegación de primer nivel, sin `Origin`, así que ninguna de las dos
capas la afecta. El riesgo residual es de *login CSRF* con un token de
verificación ajeno, que exige que el atacante posea un token válido sin usar —
en cuyo caso ya controla esa cuenta.

## Alternativas descartadas

- **Token aleatorio en tabla, con expiración propia.** Estado, purga y una
  consulta por petición para no resistir mejor el cookie tossing que el
  derivado. La única ventaja sería poder revocar un token sin revocar la
  sesión, y no hay caso de uso.
- **Comparar cookie contra cabecera a secas** (double-submit clásico). Es el
  patrón que sugería el informe y es el que el subdominio comprometido —el
  escenario que motiva todo esto— sabe romper.
- **Exigir `Origin` siempre**, rechazando cuando falta. Rompe Flutter nativo, el
  webhook de Stripe y cualquier script, y no gana nada: el ataque que
  perseguimos siempre trae `Origin`.
- **Emitir la cookie CSRF desde cada handler.** El bloque `set_cookie` estaba
  copiado en ocho sitios; sumar un noveno olvidable es exactamente el fallo que
  hay que evitar, y una cookie que falta no rompe nada visible, solo desactiva
  la comprobación. Por eso `app/auth/cookies.py`.

## Consecuencias

- La sesión son **dos cookies** que se emiten y se borran juntas, desde
  `set_session_cookies` / `clear_session_cookies`. Un handler nuevo que abra
  sesión llama a ese helper, nunca a `set_cookie` a mano.
- **Las dos capas salen en `enforce`**, y eso invierte la regla de siempre para
  una de ellas. `GAIA_CSRF_ORIGIN_CHECK` no pide nada a los clientes, pero
  `GAIA_CSRF_TOKEN_CHECK` exige la cabecera: **el backend no puede llegar a
  producción antes que React y Flutter**. Un navegador con el bundle cacheado
  que aún no la manda recibe 403 en toda mutación, y lo mismo una instalación
  self-hosted cuyo frontend actualice más tarde. La salida es bajar la variable
  a `log` —registra sin bloquear— y no necesita redespliegue.
- El `client` de `tests/conftest.py` reenvía `ga_csrf` en la cabecera con un
  hook de httpx, igual que hacen React y Flutter. Sin eso, el flip a `enforce`
  obligaría a tocar 1954 tests.
- `startup_checks` avisa cuando alguna capa está en `off` o en `log`, y da
  `error` si el modo no se reconoce — un typo no puede dejar la protección
  apagada en silencio.
- Una integración que llame a la API desde un navegador y un origen distinto
  necesita estar en `GAIA_CORS_ORIGINS`. Hoy no hay ninguna.
