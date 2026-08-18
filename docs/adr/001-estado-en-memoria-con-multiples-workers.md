# 001 · Estado en memoria con múltiples workers

- **Fecha**: 2026-08-16 (la decisión es anterior; esto solo la saca del código)
- **Estado**: aceptada; la parte del rate limiter la cierra el [009](009-cuota-compartida-y-por-principal.md)
- **Afecta a**: `main.py`, `app/middleware/ratelimit.py`, `app/storage/guest.py`,
  `app/config/server.py`, y a cualquier despliegue con `GAIA_WORKERS > 1`

## Contexto

uvicorn arranca `GAIA_WORKERS` **procesos independientes**. Cada uno ejecuta su
propio lifespan y tiene su propia memoria. Dos piezas del backend guardan estado
en memoria de proceso, y ninguna de las dos lo tenía en cuenta:

**Las sesiones de invitado** (`app/storage/guest.py`) viven en un dict de
proceso, no en la BD que sí comparten los workers. Sin afinidad de sesión en el
proxy, dos peticiones del mismo invitado caen en workers distintos:
`get_session` crea una vacía y el agente que el invitado acababa de crear
desaparece. El tope `MAX_SESSIONS` también es por proceso, así que el límite
real es `workers × MAX_SESSIONS`. El fallo era silencioso y solo se veía en
producción.

**El rate limiter** (`RateLimiter`) cuenta en memoria de proceso, así que el
límite efectivo era el declarado multiplicado por `WORKERS`: con el default de
4, los 5 intentos de login se convertían en 20.

## Decisión

**Sesiones de invitado**: no se persisten. `main.py` emite un `flog.warning` en
el arranque cuando `GAIA_WORKERS > 1` y el modo invitado está activo, indicando
el tope real de sesiones. El fallo sigue existiendo, pero deja de ser silencioso.

**Rate limiter**: el constructor reparte la cuota entre procesos
(`math.ceil(calls / _WORKERS)`), **redondeando hacia arriba**. Con `calls=5` y 4
workers, `5 // 4 = 1` dejaba un solo intento por proceso: quien se equivocaba una
vez de contraseña y reintentaba sobre la misma conexión keep-alive recibía un 429
con `Retry-After: 300`. Afectaba a login, registro, alta de invitado y
recuperación de contraseña, los cuatro con límite 5. Pasarse un poco del límite
declarado (8 en el clúster en vez de 5) es preferible a bloquear al usuario
legítimo.

**Migración de esquema**: se ejecuta una sola vez en el proceso maestro, antes
de que uvicorn lance los workers. Sin eso competirían por crear las mismas
tablas e índices en paralelo contra una BD recién creada.

## Alternativas descartadas

- **Persistir la sesión de invitado en la BD** — cambia su contrato de demo
  efímera, obliga a contemplarla en el borrado RGPD y añade filas de usuarios no
  registrados con su coste de limpieza. Es una decisión de producto, no técnica,
  y no está tomada.
- **Afinidad de sesión en el proxy** — más barata y resuelve el síntoma del
  invitado, pero no el del rate limiter ni el tope de sesiones.
- **Contador de rate limit fuera del proceso (Redis/BD)** — es lo correcto si el
  límite tiene que ser **exacto**, y para el login probablemente deba serlo. No
  se hizo *aquí*: era otra conversación. Se hizo después contra la BD, sin
  Redis, y hoy lo usan todos los limiters de ruta — ver el
  [009](009-cuota-compartida-y-por-principal.md).

## Consecuencias

- El límite de rate limiting es aproximado por diseño: se pasa por arriba, nunca
  por abajo. **Esto ya no describe la ruta de producción**: desde el 009 todos
  los limiters de ruta cuentan en la BD y el límite declarado es el del clúster.
  El reparto sigue vivo para los limiters sin nombre estable, que hoy son solo
  los de los tests.
- Con `GAIA_WORKERS > 1` y sin sticky sessions, el invitado pierde su trabajo
  entre peticiones. Está avisado en el arranque, no resuelto.
- Cualquier estado nuevo en memoria de proceso hereda este problema. Antes de
  añadir uno, decidir explícitamente qué pasa con N workers.
