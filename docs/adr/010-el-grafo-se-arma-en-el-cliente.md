# 010 · El backend entrega relaciones; el grafo lo arma el cliente

- **Fecha**: 2026-08-18
- **Estado**: aceptada
- **Afecta a**: `backend_fastapi` — `app/services/resource_relations.py`,
  `app/api/routes/explore.py`, `app/api/routes/admin/explore.py`,
  `app/services/official_source_drafts.py`; `app_flutter` —
  `lib/shared/graph/resource_graph_builder.dart` y las cinco pantallas que
  abren un grafo

## Contexto

El grafo de recursos **se dibujaba** en un solo sitio —`AnimatedResourceGraph`
sobre `CustomPaint`, sin ningún paquete de grafos— y eso nunca estuvo en
discusión. Lo que estaba escrito **ocho veces** era el paso anterior: recorrer
un recurso y convertir sus relaciones en nodos y aristas. Unas 1.350 líneas
repartidas en cuatro constructores de Dart y cuatro endpoints de Python.

No eran ocho grafos distintos:

- Cuatro contenían la misma frase: «un agente usa una skill, un prompt, una
  tool, un knowledge, un pack, una conexión y su memoria».
- Tres contenían «un pack contiene carpetas y las carpetas contienen ficheros»,
  con el mismo recorrido de rutas relativas.
- El **mismo grafo del mismo pack oficial** se armaba dos veces según por qué
  botón se entrara: en Python desde la tarjeta de Explorar, en Dart desde la
  página de detalle del pack.

Y ya habían divergido, que es el daño real y no la duplicación en sí: el grafo
de un mismo agente enseñaba conexión, skills, prompts, tools, knowledge, packs
con sus ficheros y memoria, con nombres legibles, si se abría desde Agentes;
pero solo conexión, skills, knowledge y memoria —etiquetados con el id crudo—
si se abría desde Workflows. Nadie decidió eso: cada pantalla tomó el camino
corto con el modelo que tenía a mano.

El reparto tampoco respondía a ninguna decisión registrada. El caso peor era
`GET /api/admin/resources/{tipo}/{id}/graph`, documentado como «el vecindario
relacional **inmediato** de un recurso», que empezaba cargando once listados
completos sin filtro (~24 consultas), todos los ficheros de todos los packs,
todas las cuentas de todos los usuarios y una consulta por fuente oficial —
para acabar dibujando media docena de aristas. Su coste crecía con el tamaño de
la instalación, no con el del grafo.

## Decisión

**El servidor entrega hechos; el cliente decide la forma.**

Los endpoints devuelven relaciones planas y no un grafo montado:

```jsonc
{
  "root": { "type": "agent", "id": "ag-1", "label": "Redactor" },
  "items": [
    { "type": "skill", "id": "sk-1", "label": "Resumir",
      "relation": "uses", "via": null, "inverse": false },
    { "type": "knowledge", "id": "k-9", "label": "tono.md",
      "relation": "contains", "via": {"type": "knowledge_pack", "id": "kp-3"},
      "path": "guia/tono.md" }
  ]
}
```

- `via` es el recurso del que cuelga —un par (tipo, id), porque el mismo id
  puede existir en dos tipos—; `null` significa que cuelga de la raíz.
- `inverse` invierte la arista: un propietario, una fuente oficial o el agente
  que usa el recurso apuntan **hacia** aquello de lo que cuelgan.
- `path` viaja solo en los miembros de un pack. **El servidor ya no inventa
  nodos de carpeta**: manda la ruta y el árbol lo construye el cliente. Esa es
  exactamente la lógica que estaba escrita tres veces.
- `relation` deja de descartarse: el cliente lo traduce a estilo de arista en
  un único sitio. Antes el backend lo calculaba y serializaba en cada respuesta
  y el cliente ni siquiera lo deserializaba.

El ensamblado vive en un solo fichero, `resource_graph_builder.dart`, tanto
para lo que el cliente ya tiene cargado (Agentes, Workflows, Knowledge, packs
oficiales) como para lo que llega por la red (`fromRelations`).

## Por qué el backend sigue participando

Hay dos cosas que un cliente no puede resolver, y son la razón de que estos tres
endpoints existan:

1. **`public_dependencies`**: qué dependencias de un recurso publicado son
   visibles. Filtrar es precisamente no enviar; un cliente no puede filtrar lo
   que no debería haber recibido.
2. **Los recursos de otros usuarios** en el panel de administración.

Fuera de eso, el cálculo se hace en la máquina del usuario, que está parada,
en vez de en CPU compartida por toda la instalación.

## Alternativas descartadas

- **Llevarlo todo al servidor** (que las cinco pantallas de Dart pidieran su
  grafo). Un solo sitio, sí, pero va contra el reparto de coste y mete una
  petición HTTP donde hoy el grafo abre al instante.
- **Un constructor genérico único** que sirviera para las ocho llamadas. Cada
  pantalla parte de un modelo distinto; un constructor para todos acaba siendo
  un árbol de condicionales peor que las ocho funciones. Lo que se unifica son
  las **piezas** —`_agentSubgraph`, `_packTree`— no la firma.
- **Mantener los `/graph` junto a los `/relations`**. Dos formatos vivos es la
  situación de partida con otro nombre.

## Consecuencias

- El grafo de un recurso es el mismo se abra desde donde se abra. El de una
  orquestación pasa a tener **más nodos** que antes: cada paso enseña ahora
  también los prompts, las tools y los packs de su agente.
- `admin_resource_graph` (543 líneas, la función más larga de las ocho) se
  sustituye por consultas dirigidas. Queda un único recorrido de tabla
  completa, inevitable: «qué agentes usan este recurso» no se puede filtrar en
  SQL porque las referencias viven dentro de un JSON. Antes se recorrían once.
- Los cuatro `GET …/graph` se retiraron y `tests/api/contrato_rutas.txt` se
  regeneró. **La retirada fue el último paso**: primero se añadieron los
  `/relations`, después migró Flutter, y solo entonces se borraron los viejos —
  un bundle cacheado que siguiera pidiendo `/graph` habría dejado de funcionar.
  Es la misma lección que dejó el token CSRF (ver ADR 006).
- Dos guardas impiden que vuelva a repartirse:
  `tests/api/test_grafo_en_un_sitio.py` falla si una ruta devuelve `root_id`
  con `nodes`, si un diccionario declara `source_id` junto a `target_id` fuera
  del servicio, o si reaparece una ruta acabada en `/graph`; y en Flutter,
  `test/feature_architecture_test.dart` falla si se construye un `GraphNode` o
  un `GraphEdge` fuera de `lib/shared/graph/`.
