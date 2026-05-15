<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/config.md">🇬🇧 Read in English</a>
</div>

<br>

# Configuración

Toda la configuración se realiza mediante variables de entorno. Estas se establecen desde el orquestador de despliegue ([iAgentsHub](https://github.com/iagentshub/iagentshub)) y no requieren modificar ningún fichero de código.

---

## Qué se puede configurar

| Ajuste | Descripción |
|---|---|
| Secreto de sesión | Clave usada para firmar los tokens de sesión. Obligatorio en producción. |
| Directorio de datos | Ruta donde se almacenan agentes, conexiones, skills y memoria. |
| Puerto y host | Dónde escucha el servidor. |
| Orígenes permitidos | Dominios desde los que se permite el acceso a la API. |
| Duración de sesión | Tiempo en horas que una sesión permanece activa. |
## Secreto de sesión

Debe generarse de forma aleatoria antes del primer arranque y no cambiarse mientras haya sesiones activas. Si no se configura, el sistema usa un valor almacenado en los datos de la plataforma — aceptable en desarrollo, no en producción.

Este secreto también actúa como clave maestra para cifrar las API keys almacenadas en la base de datos (derivada mediante PBKDF2-SHA256). **Cambiarlo después de haber guardado API keys hará que esas claves sean ilegibles** — los usuarios tendrán que volver a introducir sus credenciales.
