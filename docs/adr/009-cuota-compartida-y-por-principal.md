# 009 · La cuota de rate limit se comparte entre workers y se cuenta por principal

- **Fecha**: 2026-08-18
- **Estado**: aceptada
- **Afecta a**: `app/middleware/ratelimit.py`, `app/config/session.py`,
  `app/config/startup_checks.py`, `app/api/app.py`, y los ocho módulos de rutas
  con limiter (`agent_chat`, `connection_diagnostics`, `connection_sync`,
  `billing`, `social`, `accounts`, `auth/*`)
- **Continúa**: [001](001-estado-en-memoria-con-multiples-workers.md), que dejó
  esto como consecuencia abierta

## Contexto

`RateLimiter` soportaba `shared=True` —contador en la tabla
`rate_limit_windows`, atómico entre procesos— desde que se cerró el 001, pero
**solo lo usaban los seis limiters de autenticación**. Los otros doce seguían
contando en un `OrderedDict` del proceso: chat, prueba de conexiones, sync del
hub, billing, social y device flow. El mecanismo estaba hecho y probado; lo que
faltaba era aplicarlo, y nada avisaba de que faltara.

Con `GAIA_WORKERS=4` eso significaba tres cosas a la vez:

- El límite declarado se reparte entre procesos, así que **el número escrito en
  el código no es el límite del clúster**. `RATE_CHAT_CALLS=30` era 8 por
  worker, y cuál de ellos atiende la petición depende del balanceo.
- **Se pierde entero en cada reinicio o redeploy.** Quien reintenta justo
  después empieza de cero.
- **La clave era la IP en endpoints autenticados**, que falla en las dos
  direcciones: detrás de un NAT corporativo toda la oficina comparte cupo, y
  quien rota IPs no encuentra techo. Para el chat —el endpoint que gasta dinero
  en llamadas al LLM— la unidad que consume es la cuenta, no la ruta de red.

Cuatro de esos doce, además, **estaban declarados y nadie los aplicaba**:
`agents._chat_limiter` y los tres de `connections.py` se quedaron atrás cuando
sus endpoints se extrajeron a `agent_chat.py`, `connection_diagnostics.py` y
`connection_sync.py`. No limitaban nada y no había forma de notarlo leyendo el
módulo, porque el nombre seguía ahí arriba.

## Decisión

**Todo limiter de ruta comparte su cuota.** Los quince que quedan van con
`shared=True` y nombre estable; los cuatro muertos se borran.
`test_todo_limiter_de_ruta_comparte_su_cuota` recorre `app/api/routes/` y falla
si aparece uno con contador de proceso, y `test_ningun_limiter_de_ruta_esta_sin_usar`
si aparece otro declarado que nadie aplica.

**La clave es el principal cuando lo hay.** `principal_key()` resuelve la
identidad **sin tocar la base de datos** —hash del PAT, o el `sub` del JWT ya
firmado— porque corre antes que la dependency de autorización y en la ruta
caliente del chat. No autoriza nada: un token falso cae a la rama de IP y el 401
lo da después quien corresponde. Los limiters de autenticación se quedan por IP,
que es la única identidad que existe antes de tener sesión.

**Encima de la cuota por usuario hay un techo por IP**, `GAIA_RATE_IP_FACTOR`
veces más laxo (5 por defecto). La clave por principal, sola, regala un cupo
entero por cada cuenta desechable que alguien registre. A 0 se desactiva, y la
auditoría de arranque lo dice: es una decisión legítima para una instalación
interna, pero no puede tomarse en silencio.

**El bucle de mantenimiento purga la tabla cada 6 h.** El UPSERT reinicia la
ventana de quien vuelve; la fila de quien no vuelve se quedaba para siempre, una
por cada (limiter, principal) que pasó una vez. El horizonte sale de la ventana
más larga registrada en el proceso, no de una constante: purgar con el corte de
60 s de los demás le devolvería la cuota a `auth-forgot`, que la tiene de una
hora.

## Alternativas descartadas

- **Redis.** Es el backend correcto para esto y sigue siéndolo, pero añade una
  dependencia de infraestructura a un producto que se instala con un
  `docker compose up`. La tabla ya está y el reemplazo no toca a los llamantes:
  `_consume_shared` es el único sitio que sabe dónde vive el contador.
- **Cachear la cuota en memoria y sincronizar cada N peticiones.** Ahorra
  consultas y devuelve exactamente el problema que esto viene a quitar: el
  límite vuelve a ser aproximado y a depender de qué worker atendió.
- **Resolver el PAT a usuario para agrupar sus peticiones con las de la sesión
  del mismo dueño.** Cuesta una consulta más por request en la ruta caliente
  para unificar dos claves que, en la práctica, son dos clientes distintos.

## Consecuencias

- El límite declarado pasa a ser el del clúster y sobrevive a los redeploys. En
  una instalación con varios workers eso significa que **el límite efectivo baja
  hasta el que dice el código**: quien se apoyaba sin saberlo en el reparto verá
  429 antes.
- Cada comprobación toca la base de datos, y las que tienen techo por IP la
  tocan dos veces. Son dos UPSERT por índice primario frente a una llamada al
  LLM que tarda segundos; el peor caso medido no es el chat sino
  `/connections/test-all`, que ya sale a la red por cada conexión.
- El contador en memoria sigue existiendo para limiters sin nombre estable —los
  tests lo usan—, con su `ponytail:` explicando el techo. La ruta de producción
  ya no pasa por ahí.
- La tabla `rate_limit_windows` es ahora estado operativo con volumen: crece con
  usuarios e IPs y depende de un bucle de fondo para no hacerlo sin límite. Si
  el bucle muere, el síntoma es una tabla grande, no un límite mal aplicado.
