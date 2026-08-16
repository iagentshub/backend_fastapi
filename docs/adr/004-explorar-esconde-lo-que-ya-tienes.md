# 004 · Explorar enseña lo que no tienes, y el estado vive en el servidor

- **Fecha**: 2026-08-16
- **Estado**: aceptada
- **Afecta a**: `app/api/routes/explore.py`, `app/services/official_pack_service.py`,
  `app/pagination/http.py`, `app/api/app.py` (CORS), y en `app_flutter` la
  feature `explore` completa

## Contexto

El catálogo devolvía también los recursos de los que el usuario ya tenía una
copia enlazada, con el botón «Enlazar» activo. La única señal de «ya lo tengo»
era `_linkedKeys` en el controlador de Flutter: un `Set` que se llenaba al
pulsar el botón y **se perdía al recargar la pantalla**. El resultado es que
Explorar ofrecía una y otra vez lo que el usuario ya había traído, y la segunda
pulsación fallaba en el backend o creaba una copia más.

El dato existía desde siempre: enlazar escribe una fila en `resource_social`
con `linked_to_id` y `linked_to_user` apuntando al original. Nadie lo estaba
leyendo desde el catálogo.

## Decisión

**El parámetro `relation` (`all` | `new` | `linked`) filtra en SQL.** No en el
cliente: la ruta pagina con `LIMIT/OFFSET` y publica el total en
`X-Total-Count`, así que un filtro en cliente dejaría el total mintiendo,
páginas a medio llenar y un «cargar más» que no dispara.

La condición es un `EXISTS` correlacionado con las cuatro columnas en el orden
de `idx_rsoc_link_origin` (`owner`, `linked_to_user`, `linked_to_id`,
`resource_type`), el índice parcial que ya existía para el origen de las copias.
Sin `linked_to_user` —que está en la fila del catálogo— el índice solo se
usaría como rango por `owner`.

**El valor por defecto de la API es `all`; quien pide `new` es la app.** La
ruta es pública y estable: añadir un parámetro no puede cambiar lo que ve un
cliente que no lo envía. La decisión de producto —descubrir es ver lo que no
tienes— es del cliente, y ahí el valor por defecto es `new`.

**`linked_by_me` viaja en cada fila.** En `relation=all` conviven los dos
estados y la tarjeta necesita saber cuál está pintando.

**Un pack oficial solo cuenta como enlazado si lo están todos sus
componentes.** `link_state` ya distinguía `none | partial | complete`. Tratar el
pack como un booleano escondería del catálogo los componentes que el usuario
todavía no tiene.

**El vacío se explica con la cabecera `X-Linked-Count`,** que solo se calcula
cuando `relation=new` devuelve la primera página vacía. Buscar por nombre algo
que ya tienes y recibir «no hay resultados» parece un buscador roto; con el
conteo, el cliente ofrece saltar a «Enlazados». La cabecera vive en
`app/pagination/http.py` junto al resto de metadatos de página **porque esa
lista es la que `app/api/app.py` pasa a `expose_headers`**: una cabecera
declarada en otro sitio llega vacía a Flutter Web sin que nada falle.

## Alternativas descartadas

- **Un botón booleano «ver lo que tengo»** — deja fuera el estado «todos».
  Alguien que busca por nombre un recurso ya enlazado no lo encuentra en ningún
  sitio y nada le explica por qué. Con tres estados el vacío es explicable.
- **Filtrar en el cliente sobre la página recibida** — descrito arriba: rompe
  la paginación. Es además el error que ya cometía `_linkedKeys`, a menor escala.
- **Una segunda petición del cliente para contar lo excluido** — era la primera
  versión. Un `round-trip` extra en cada carga vacía, y rompía el test que
  vigila que el debounce agrupe los cambios de filtro en una sola llamada. La
  cabecera cuesta un `COUNT` que solo se ejecuta cuando no hay nada que devolver.
- **Poner `linked_copy_id` en la respuesta para abrir «mi copia»** — no hay
  navegación por id de recurso en Flutter (`AppRouter` llega a la lista, no al
  detalle), así que sería superficie de API sin consumidor.

## Consecuencias

Enlazar desde Explorar ya no ofrece el mismo recurso otra vez tras recargar.

La vista «Enlazados» muestra el original público, no la copia del usuario, así
que **no puede avisar de un enlace roto**: si el original deja de ser público
desaparece del catálogo por definición. Ese aviso pertenece a la pantalla de
cada recurso, que es donde `my_resources` ya calcula `linked_broken`.

Sincronizar y desvincular siguen viviendo en la pantalla del recurso enlazado,
no en el catálogo.
