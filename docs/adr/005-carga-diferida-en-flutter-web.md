# 005 · La web no descarga admin, workflows ni el checkout hasta que se entra

- **Fecha**: 2026-08-16
- **Estado**: aceptada
- **Afecta a**: `app_flutter/lib/app/router/internal_router.dart`,
  `app_flutter/lib/app/router/deferred_page.dart`,
  `app_flutter/tool/check_web_bundle_size.sh`,
  `app_flutter/.github/workflows/flutter-ci.yml`

## Contexto

No había ni una sola importación `deferred as` en las fuentes de `lib/`. Todo lo
alcanzable desde `main()` —el panel de administración con las pestañas de estrés
de Centinel, el editor visual de workflows con `vyuh_node_flow`, la orquestación
LLM, el checkout de Stripe— se descargaba **antes de que el usuario viera la
pantalla de login**.

El canal principal del producto es la web (`/app/` tras el landing de React),
donde el tamaño del bundle es tiempo hasta la primera interacción. Y el reparto
era especialmente desfavorable: las áreas más pesadas las usa una fracción
pequeña de la gente —admin, solo los administradores— y las pagaba todo el
mundo en cada primera carga.

Medido antes de tocar nada, con Flutter 3.44.8 y `flutter build web --release`:
`main.dart.js` = **5.932.241 B**, en una sola pieza.

## Decisión

**Cinco librerías se importan con `deferred as` en `internal_router.dart`**, que
es el único fichero que las alcanza: `admin_page`, `centinel_page`,
`metadata_page`, `workflows_page` y `checkout_page`. dart2js reparte lo que solo
ellas usan en partes que se piden al entrar en la ruta.

Resultado de la misma build tras el cambio: `main.dart.js` = **5.155.323 B**,
**−776.918 B (−13,1 %)**, más 11 partes que suman 799.444 B y que solo descarga
quien entra en esas secciones. Las dos grandes son admin (343 KB) y el editor de
workflows (300 KB).

**`DeferredPage` monta la ruta**, no un `FutureBuilder` suelto, por dos motivos
que no son cosmética:

- *Recuerda lo ya cargado.* `loadLibrary()` está cacheado, pero un
  `FutureBuilder` pinta igualmente un frame de espera en cada entrada, así que
  volver a Admin parpadearía siempre.
- *Deja salir del error.* Pedir la parte es una petición HTTP más: se corta la
  red, o un despliegue nuevo retira el fichero que la pestaña abierta todavía
  pide. Sin reintento la sección queda muerta hasta recargar la pestaña entera.
  El fallo se registra en `AppDiagnostics` como `deferred.<parte>`.

**El diferido se protege con dos guardarraíles**, porque se deshace sin ruido:
basta que otro fichero importe una de esas páginas sin `deferred` para que su
código vuelva al bundle principal, y ningún test de comportamiento cambia.

- `app_flutter/test/deferred_routes_test.dart` — el router importa las cinco con
  `deferred as`, nadie más las importa sin diferir, y cada `DeferredPage` nombra
  su propio prefijo.
- `app_flutter/tool/check_web_bundle_size.sh`, en CI tras `flutter build web
  --release` — falla si `main.dart.js` supera 5.500.000 B (~5,25 MiB) o si la
  build no generó ninguna parte. El umbral está escrito a mano en el script para
  que subirlo aparezca en el diff.

## Alternativas descartadas

**Diferir también el arranque de Stripe.** `main.dart` importa `flutter_stripe`
y fija `Stripe.publishableKey` en el arranque cuando hay clave configurada, así
que ese trozo sigue en el bundle principal; lo que sale es
`flutter_stripe_web` a través del checkout. Moverlo exigiría configurar Stripe
justo antes de montar el elemento de pago, y equivocarse ahí rompe el cobro.
Queda como mejora medible aparte, no dentro de este cambio.

**Diferir la feature `workflows` entera.** No se puede: `app.dart` y
`AppServicesScope` construyen `WorkflowRunsController` en el arranque para poder
avisar de ejecuciones en curso desde cualquier pantalla. Lo diferido es la
página —con el editor visual y `vyuh_node_flow`, que es donde está el peso—, no
el controlador.

**Diferir `shared/graph/`.** El grafo animado lo alcanzan Explorar y Knowledge,
que no son diferidos; su código quedaría en el bundle principal de todas formas.

**Tocar los 512 KB de `assets/locales/`.** Se comprobó y no hacía falta: en la
build web quedan como ficheros sueltos bajo
`build/web/assets/assets/locales/{es,en}/*.json`, uno por namespace, y
`LocaleLoader` pide por HTTP solo el que necesita. De la carga inicial solo
cuelga el `AssetManifest.bin` (5 KB), que sí los lista todos. El peso está en
disco, no en el arranque.

**Medir el bundle comprimido.** gzip/brotli dependen del servidor y de su
versión, así que el número variaría entre entornos por motivos ajenos al código.
El presupuesto mide el fichero sin comprimir, que es igual en todas partes.

## Consecuencias

- La primera entrada a admin, workflows o checkout añade la latencia de
  descargar su parte: se ve un indicador de carga donde antes la página aparecía
  ya construida. Las siguientes visitas son instantáneas.
- Un despliegue nuevo puede invalidar las partes que una pestaña vieja todavía
  no ha pedido. Ese caso cae en el reintento de `DeferredPage`; recargar la
  pestaña lo resuelve.
- Añadir una pantalla pesada nueva sin diferirla no falla ningún test hasta que
  el bundle cruza el presupuesto. Es deliberado: el umbral tiene margen para una
  feature, no para un módulo entero.
- Fuera de web (`flutter test` incluido) `loadLibrary()` resuelve sin descargar
  nada, así que móvil y escritorio pagan a lo sumo un frame de más.
